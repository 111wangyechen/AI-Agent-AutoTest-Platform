"""
通用内核 · 主控 Agent（编排层）

Coordinator 持有一个插件，构造 5 个子 Agent，按测试流水线串行编排：
    需求分析 → 用例设计 → 执行 → 结果分析 → 报告生成
artifact 逐段传递。某段抛异常时降级：记录到 stage_errors 并尽力继续，
最终报告里如实标注哪一段失败——测试平台不能因为一段崩了就整个哑掉。

「换插件即换测试域、同一 run()」在这里落地：Coordinator 对插件零硬编码。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from core.config import config
from core.observability import RunBudget, Tracer
from core.plugins import (
    Diagnosis,
    RawData,
    TestCase,
    TestPlugin,
    TestReport,
    TestRequirement,
)
from .subagents import (
    AnalysisAgent,
    CaseAgent,
    ExecutorAgent,
    ReportAgent,
    RequirementAgent,
)

OnEvent = Callable[[str, Any], None] | None

# 流水线阶段名（checkpoint 用它记录进度）
STAGES = ["requirement", "case_gen", "execute", "analyze", "report"]

# 分析→执行 自主复测循环上限（避免无限循环）
MAX_REPRODUCE_ROUNDS = 2


class Coordinator:
    """主控：编排 5 个子 Agent 跑完一次测试，支持 checkpoint / resume。"""

    def __init__(
        self,
        plugin: TestPlugin,
        workspace_path: str | None = None,
        budget: RunBudget | None = None,
    ) -> None:
        self.plugin = plugin
        self.requirement_agent = RequirementAgent(plugin)
        self.case_agent = CaseAgent(plugin)
        self.executor_agent = ExecutorAgent(plugin)
        self.analysis_agent = AnalysisAgent(plugin)
        self.report_agent = ReportAgent(plugin)

        # checkpoint 相关（workspace_path 为 None 时禁用打点，保持旧行为）
        self.workspace_path = workspace_path
        self._store = None
        if workspace_path:
            from core.checkpoint import CheckpointStore
            self._store = CheckpointStore(workspace_path)
        # resume 时由 restore_state 填充
        self._restored: dict[str, Any] = {}

        # P5 可观测 + 预算：显式传入的 budget 优先；否则按 config 装配
        # （未配环境变量时 max_*=None → 不设限，行为与升级前完全一致）。
        self.budget = budget if budget is not None else RunBudget(
            max_total_tokens=config.max_total_tokens,
            max_wall_clock_s=config.max_wall_clock_s,
        )

    def run(self, requirement: str, on_event: OnEvent = None, _resume_ckpt=None) -> TestReport:
        """
        跑完整流水线，返回 TestReport。

        _resume_ckpt: 内部参数，resume 时传入 checkpoint，用于跳过已完成阶段、
        复用已产出的 artifact。正常调用不传。
        """
        errors: list[str] = []
        done = set(self._restored.get("completed_stages", []))  # resume 时已完成的阶段
        test_id = self._restored.get("test_id") or datetime.now().strftime("%Y%m%d_%H%M%S")

        # P5 可观测 + 预算：Tracer 旁路订阅事件流（累积 token/耗时并喂预算），
        # 用 multicast 把每个事件同时投递给 Tracer 和外部 on_event——下游零改动，
        # 既能展示又能被统计。budget.start() 作为墙钟止损的计时原点。
        _external_on_event = on_event
        tracer = Tracer(budget=self.budget, downstream=_external_on_event)
        self._tracer = tracer
        self.budget.start()

        def on_event(kind: str, payload: Any) -> None:  # noqa: 多播，遮蔽入参同名
            tracer.on_event(kind, payload)
            if _external_on_event:
                _external_on_event(kind, payload)

        budget_aborted = False  # 预算越界提前终止标志（供 report 阶段诚实收尾）
        stall_aborted = False   # P9 复测停滞收敛止损标志
        stall_rounds = 0        # 停滞时已复测的轮数（供报告横幅）

        def stage(name: str) -> None:
            on_event("stage", {"stage": name})

        # 1) 需求分析（resume 且已完成则跳过）
        if "requirement" in done:
            req = TestRequirement(raw=requirement, device=self.plugin.device_desc)
        else:
            stage("需求分析")
            try:
                req = self.requirement_agent.run(requirement, on_event=on_event)
            except Exception as e:
                errors.append(f"需求分析失败: {e}")
                req = TestRequirement(raw=requirement, device=self.plugin.device_desc)
            done.add("requirement")
            self._checkpoint("requirement", list(done),
                             {"requirement_raw": requirement}, test_id=test_id)

        # 2) 用例设计
        cases: list[TestCase] = []
        if "case_gen" in done:
            pass  # 用例矩阵是确定性的，需要时重新生成即可
            try:
                cases = self.case_agent.run(req, on_event=on_event)
            except Exception:
                pass
        else:
            stage("用例设计")
            try:
                cases = self.case_agent.run(req, on_event=on_event)
            except Exception as e:
                errors.append(f"用例设计失败: {e}")
            done.add("case_gen")
            self._checkpoint("case_gen", list(done),
                             {"requirement_raw": requirement, "case_count": len(cases)},
                             total_cases=len(cases), test_id=test_id)

        # 3) 执行采集
        raw: list[RawData] = []
        if "execute" in done:
            # 已采数据在 workspace，避免重复独占设备/重播音频。
            # 关键：从落盘的 raw_data.json 重建 raw，让 analyze/report 有数据可用，
            # 否则 resume 会跳过执行但分析拿到空数据、报告是空壳。
            raw = self._load_raw_data()
        else:
            stage("执行采集")
            try:
                raw = self.executor_agent.run(cases, on_event=on_event)
            except Exception as e:
                errors.append(f"执行采集失败: {e}")
            # 先落盘再打点：确保 checkpoint 落地时数据一定已在 workspace
            self._save_raw_data(raw)
            done.add("execute")
            self._checkpoint("execute", list(done),
                             {"requirement_raw": requirement},
                             case_index=len(raw), total_cases=len(cases), test_id=test_id)

        # 4) 结果分析 + 自主复测循环（分析→执行 回退）
        #
        # 分析 Agent 判断证据不足时（need_reproduce=true），Coordinator 带着 hint
        # 回到执行阶段自主复现并补采证据。最多复测 MAX_REPRODUCE_ROUNDS 轮，
        # 避免陷入死循环。每轮的 raw_data 追加到 workspace/raw_data_round{N}.json
        # 保留完整证据链供研发回溯。
        diagnosis: Diagnosis | None = None
        if "analyze" in done:
            diagnosis = self._load_diagnosis()  # resume：复用已产出的诊断
        elif self.budget.exceeded():
            # P5 预算止损：进入分析（最烧 token 的阶段）前已越界 → 不再启动新 LLM 阶段，
            # 诚实收尾。结论留空，报告如实标注「因预算/时限提前终止」。
            budget_aborted = True
            errors.append(self._budget_abort_note("结果分析"))
            on_event("stage_skipped", {"stage": "结果分析", "reason": "budget"})
        else:
            reproduce_rounds = 0
            while True:
                stage("结果分析" if reproduce_rounds == 0
                      else f"结果分析（复测第 {reproduce_rounds} 轮后）")
                try:
                    diagnosis = self.analysis_agent.run(
                        self._analysis_task(requirement, raw), on_event=on_event
                    )
                except Exception as e:
                    errors.append(f"结果分析失败: {e}")
                    break

                # 到达硬上限 or 模型不要求复测 → 退出（原有兜底逻辑保留）
                if not (diagnosis and diagnosis.need_reproduce
                        and reproduce_rounds < MAX_REPRODUCE_ROUNDS):
                    break

                # P5：复测会再烧一轮分析 token，开新一轮前先查预算越界，越界即诚实收尾。
                if self.budget.exceeded():
                    budget_aborted = True
                    errors.append(self._budget_abort_note(f"自主复测第 {reproduce_rounds + 1} 轮"))
                    on_event("stage_skipped",
                             {"stage": "自主复测", "reason": "budget"})
                    break

                # P9：复测前记证据指纹，用于复测后判断有没有实质增量（停滞检测）
                prev_fp = self._evidence_fingerprint(raw)

                reproduce_rounds += 1
                if on_event:
                    on_event("reproduce_request", {
                        "round": reproduce_rounds,
                        "hint": diagnosis.reproduce_hint,
                        "target_evidence": diagnosis.target_evidence,
                    })
                stage(f"自主复测第 {reproduce_rounds} 轮")
                try:
                    extra_raw = self.executor_agent.reproduce(
                        cases, diagnosis, on_event=on_event
                    )
                except AttributeError:
                    # 老版 executor 没有 reproduce 方法 → 退化为普通 run
                    extra_raw = self.executor_agent.run(cases, on_event=on_event)
                except Exception as e:
                    errors.append(f"复测第 {reproduce_rounds} 轮失败: {e}")
                    break
                # 追加到主 raw_data，同时单独存一份供研发回溯
                raw.extend(extra_raw)
                self._save_reproduce_round(reproduce_rounds, extra_raw,
                                            diagnosis.reproduce_hint)
                self._save_raw_data(raw)  # 更新主证据

                # P9 停滞检测：这一轮复测采到的证据和复测前指纹一致 → 没带来新东西，
                # 别再空转到硬上限，提前收敛转人工。budget/stall 收口在 _reproduce_should_stop。
                new_fp = self._evidence_fingerprint(raw)
                should_stop, reason = self._reproduce_should_stop(prev_fp, new_fp, diagnosis)
                if should_stop and reason == "stall":
                    stall_aborted = True
                    stall_rounds = reproduce_rounds
                    errors.append(
                        f"复测收敛止损[停滞]: 第 {reproduce_rounds} 轮复测未带来新证据，提前转人工")
                    on_event("reproduce_stall", {
                        "round": reproduce_rounds,
                        "reason": "no_new_evidence",
                    })
                    break
                if should_stop and reason == "budget":
                    budget_aborted = True
                    errors.append(self._budget_abort_note(f"自主复测第 {reproduce_rounds} 轮后"))
                    on_event("stage_skipped", {"stage": "自主复测", "reason": "budget"})
                    break

                continue  # 用新证据重新分析

            # P8：置信度门槛——写库/出报告前先裁定是否需人工复核。
            # 必须在落盘前调用，让 needs_human_review 一起持久化（resume 时读回）。
            # P9：复测停滞收敛的，强制转人工（证据没涨完，结论不当定论）。
            if stall_aborted and diagnosis is not None:
                diagnosis.needs_human_review = True
            self._gate_confidence(diagnosis, on_event=on_event)
            self._save_diagnosis(diagnosis)
            done.add("analyze")
            self._checkpoint("analyze", list(done),
                             {"requirement_raw": requirement,
                              "reproduce_rounds": reproduce_rounds}, test_id=test_id)

        # 5) 报告生成
        report_md = ""
        if "report" not in done:
            if budget_aborted or self.budget.exceeded():
                # P5 预算止损：不再启动报告 LLM，确定性地产出一份诚实的中止说明，
                # 把已采证据与越界原因如实列出，而不是空烧 token 硬出报告。
                budget_aborted = True
                on_event("stage_skipped", {"stage": "报告生成", "reason": "budget"})
                report_md = self._budget_abort_report(req, cases, raw, diagnosis)
            else:
                stage("报告生成")
                try:
                    report_md = self.report_agent.run(req, cases, raw, diagnosis, on_event=on_event)
                except Exception as e:
                    errors.append(f"报告生成失败: {e}")
            # P9：复测停滞收敛止损时，确定性地在报告顶部标注收敛原因（区分三种收尾）。
            if stall_aborted:
                report_md = self._prepend_stall_banner(report_md, stall_rounds)
            # P8：防假绿的复核标记不能只靠 LLM 自觉写——这里确定性地在报告顶部置横幅，
            # 保证「低置信/证据不足需人工复核」一定可见，与模型是否照做无关。
            report_md = self._prepend_review_banner(report_md, diagnosis)
            done.add("report")
            self._checkpoint("report", list(done),
                             {"requirement_raw": requirement}, test_id=test_id)

        # 6) Golden-set 记录新 bug
        #
        # 分析 Agent 标记 is_new_bug=true 时，写入 Golden-set 供后续复用。
        # P8 防假绿：只有「高置信 + 证据充分 + 未被标记复核」的结论才升级为 CONFIRMED
        # 当定论；低置信/证据不足的照样留档，但只以 HYPOTHESIS（假设）状态入库，
        # 不伪装成已确认的定论——与升级前「新 bug 默认按 HYPOTHESIS 落库」的行为一致，
        # 高置信时才额外升级。宁可漏报不假报。
        if diagnosis and diagnosis.is_new_bug and diagnosis.bug_summary:
            confirmed = (
                not diagnosis.needs_human_review
                and diagnosis.confidence == "high"
                and diagnosis.evidence_sufficiency == "sufficient"
            )
            if on_event:
                on_event("golden_set_write", {
                    "summary": diagnosis.bug_summary,
                    "status": "confirmed" if confirmed else "hypothesis",
                    "needs_human_review": diagnosis.needs_human_review,
                })
            try:
                from core.golden_set import GoldenSet, BadCase, BadCaseStatus
                gs = GoldenSet()
                summary = diagnosis.bug_summary
                case = BadCase.new(
                    title=summary.get("title", "未命名 bug"),
                    device_model=self.plugin.device_model,
                    plugin=self.plugin.name,
                    status=BadCaseStatus.CONFIRMED if confirmed else BadCaseStatus.HYPOTHESIS,
                    tags=summary.get("tags", []) or [],
                    log_signatures=summary.get("log_signatures", []) or [],
                    symptom=summary.get("symptom", ""),
                    root_cause=summary.get("root_cause", ""),
                    layer=summary.get("layer", ""),
                    severity=summary.get("severity", "medium"),
                    fix_suggestion=summary.get("fix_suggestion", ""),
                    reproduce_steps=summary.get("reproduce_steps", []) or [],
                    evidence_paths=[str(self.workspace_path)] if self.workspace_path else [],
                )
                gs.add(case)
                if on_event:
                    on_event("golden_set_written",
                             {"case_id": case.case_id, "status": case.status.value})
            except Exception as e:
                errors.append(f"Golden-set 记录失败: {e}")

        # 7) P5 收尾：写 trace.json（配了 workspace 才落盘）+ 报告附消耗概览。
        #    即便前面降级/预算止损也照常产出，保证「每次运行都有消耗账本」。
        trace_path = self._artifact_path("trace.json")
        if trace_path is not None:
            try:
                tracer.dump(str(trace_path))
            except Exception as e:
                errors.append(f"trace 落盘失败: {e}")
        report_md = self._append_consumption_overview(report_md, tracer, budget_aborted)

        return TestReport(
            plugin=self.plugin.name,
            requirement=req,
            cases=cases,
            raw_data=raw,
            diagnosis=diagnosis,
            report_md=report_md,
            stage_errors=errors,
        )

    def analyze_only(self, task: str, on_event: OnEvent = None) -> Diagnosis:
        """只跑结果分析 Agent（等价 MVP 行为，便宜快，用于回归/演示核心价值）。"""
        return self.analysis_agent.run(task, on_event=on_event)

    # ==================== 置信度门槛（P8：防假绿）====================

    def _gate_confidence(self, diagnosis: Diagnosis | None, on_event: OnEvent = None) -> None:
        """
        证据置信度分流：低置信 / 证据不足的结论不当定论，标记为「待人工复核」。

        触发任一条件即置 needs_human_review=True：
        - confidence == "low"：模型自认根因判断把握不足；
        - evidence_sufficiency == "insufficient"：现有证据撑不起结论；
        - 矛盾信号：说证据不足却又不复测（need_reproduce=false）——不许就这么洗成通过。

        这个标志下游两处消费：Golden-set 写库时不升级为 CONFIRMED（见 run 第 6 步），
        报告里置顶「低置信结论，需人工复核」。宁可漏报不假报。
        """
        if diagnosis is None:
            return
        low = diagnosis.confidence == "low"
        insufficient = diagnosis.evidence_sufficiency == "insufficient"
        contradictory = insufficient and not diagnosis.need_reproduce
        # OR 进既有值：上游（如 P9 复测停滞）可能已置 True，这里只加不减，不覆盖
        diagnosis.needs_human_review = (
            diagnosis.needs_human_review or low or insufficient or contradictory
        )
        if diagnosis.needs_human_review and on_event:
            on_event("human_review_flagged", {
                "confidence": diagnosis.confidence,
                "evidence_sufficiency": diagnosis.evidence_sufficiency,
                "contradictory": contradictory,
            })

    @staticmethod
    def _prepend_review_banner(report_md: str, diagnosis: Diagnosis | None) -> str:
        """
        低置信/证据不足时，确定性地在报告顶部插入复核横幅。

        不依赖 ReportAgent（LLM）是否照做——防假绿是硬保证，必须由代码兜底。
        """
        if diagnosis is None or not diagnosis.needs_human_review:
            return report_md
        banner = (
            "> ⚠️ **低置信结论，需人工复核**\n"
            f"> 置信度：`{diagnosis.confidence}`，证据充分性：`{diagnosis.evidence_sufficiency}`。\n"
            "> 本结论未达自动定论门槛，不会被写入知识库为「已确认」，请人工研判后再采信。\n"
        )
        return banner + "\n" + report_md if report_md else banner

    # ==================== 可观测性 + 预算管控（P5）====================

    def _budget_abort_note(self, stage_name: str) -> str:
        """生成结构化的预算越界降级记录，入 stage_errors。"""
        st = self.budget.status()
        return (
            f"预算越界提前终止[{stage_name}]: reason={st.get('reason')}, "
            f"tokens={st.get('total_tokens')}/{st.get('max_total_tokens')}, "
            f"elapsed_s={st.get('elapsed_s')}/{st.get('max_wall_clock_s')}"
        )

    def _budget_abort_report(
        self, req: TestRequirement, cases: list[TestCase],
        raw: list[RawData], diagnosis: Diagnosis | None,
    ) -> str:
        """
        预算止损时的确定性报告：不烧 LLM，如实列出已采证据与中止原因。

        对齐红线：拿不到就明确说拿不到，不硬出一份看似完整的报告。
        """
        st = self.budget.status()
        reason_cn = {"token": "token 预算用尽", "wall_clock": "墙钟时限到达"}.get(
            st.get("reason", ""), "预算越界")
        collectors = sorted({r.collector for r in raw})
        lines = [
            "# 测试报告（因预算/时限提前终止）",
            "",
            f"> ⛔ **本次运行因「{reason_cn}」提前终止，以下结论仅基于已采集证据，未必完整。**",
            f"> 消耗：tokens={st.get('total_tokens')}/{st.get('max_total_tokens')}，"
            f"墙钟={st.get('elapsed_s')}s/{st.get('max_wall_clock_s')}s。",
            "",
            f"- 设备：{req.device or '(未知)'}",
            f"- 测试类型：{req.test_type or '(未知)'}",
            f"- 用例数：{len(cases)}",
            f"- 已采集采集器：{', '.join(collectors) if collectors else '(无)'}",
            "",
            "## 已有诊断",
            diagnosis.text if (diagnosis and diagnosis.text) else "(分析阶段未完成，无诊断结论)",
        ]
        return "\n".join(lines)

    def _append_consumption_overview(
        self, report_md: str, tracer: Tracer, budget_aborted: bool,
    ) -> str:
        """在报告末尾附「本次测试消耗概览」（token/耗时/工具调用 + 预算状态）。"""
        s = tracer.summary()
        parts = [
            report_md or "",
            "",
            "---",
            "",
            "## 本次测试消耗概览",
            "",
            f"- LLM 调用：{s['llm_call_count']} 次，"
            f"token 合计 {s['total_tokens']}（输入 {s['total_input_tokens']} / 输出 {s['total_output_tokens']}）",
            f"- LLM 耗时：{s['llm_duration_s']}s；工具调用：{s['tool_call_count']} 次"
            f"（失败 {s['tool_fail_count']}），耗时 {s['tool_duration_s']}s",
        ]
        budget = s.get("budget")
        if budget and (budget.get("max_total_tokens") or budget.get("max_wall_clock_s")):
            parts.append(
                f"- 预算：{'已越界提前终止' if budget_aborted else '未越界'}"
                f"（token {budget['total_tokens']}/{budget['max_total_tokens']}，"
                f"墙钟 {budget['elapsed_s']}s/{budget['max_wall_clock_s']}s）"
            )
        return "\n".join(parts)

    # ==================== 收敛闭环 & 复测停滞检测（P9）====================

    def _evidence_fingerprint(self, raw: list[RawData]) -> tuple:
        """
        把当前证据抽象成可比较的指纹，用于判断复测有没有带来实质增量。

        只求「稳定可比」而非精确：两轮指纹相同即视为无新证据。维度——
        - collector 种类集合（新开了某类采集器 → 指纹变）；
        - 每条 payload 的规整化摘要（status + 关键日志/指标键的集合），
          对易变噪声（时间戳、具体数值）不敏感，只认「结构性新东西」。

        刻意不把 payload 原值全量纳入——否则每轮采集的毫秒级时间戳都会让指纹
        天然不等，停滞检测就永远不触发。这里要抓的是「有没有采到新种类的证据」。
        """
        collectors = frozenset(r.collector for r in raw)
        payload_sigs: set[tuple] = set()
        for r in raw:
            payload = r.payload if isinstance(r.payload, dict) else {}
            status = payload.get("status", "")
            # payload 的键集合 + status：反映「证据的结构」，忽略具体数值波动
            keys = frozenset(k for k in payload.keys() if k != "result")
            # result 若是 dict，再纳入其键集合（如日志签名字段），仍只看结构
            result = payload.get("result")
            result_keys = (
                frozenset(result.keys()) if isinstance(result, dict) else frozenset()
            )
            payload_sigs.add((r.collector, status, keys, result_keys))
        return (collectors, frozenset(payload_sigs))

    def _reproduce_should_stop(
        self, prev_fp: tuple, new_fp: tuple, diagnosis: Diagnosis | None,
    ) -> tuple[bool, str]:
        """
        复测后的统一停机判定。返回 (是否停机, 原因)。原因取值：
        - 'budget'     预算越界（P5）——最先查，烧超了就立刻停；
        - 'stall'      证据无实质增量（P9）——复测没采到新东西，别空转到上限；
        - 'sufficient' 证据充分 / 高置信（P8）——模型说不用再复测了；
        - ''           继续复测。

        三类准则并列收口在这里，MAX_REPRODUCE_ROUNDS 退居为最后的兜底上限。
        """
        if self.budget.exceeded():
            return True, "budget"
        # 证据指纹无变化 = 这一轮复测没带来新证据 → 停滞
        if prev_fp == new_fp:
            return True, "stall"
        # 模型已明确不需要再复测（证据充分）
        if diagnosis is not None and not diagnosis.need_reproduce:
            return True, "sufficient"
        return False, ""

    @staticmethod
    def _prepend_stall_banner(report_md: str, rounds: int) -> str:
        """复测停滞收敛止损时，确定性地在报告顶部插入说明横幅。"""
        banner = (
            "> 🔁 **复测收敛止损：复测未带来新证据**\n"
            f"> 已自主复测 {rounds} 轮，证据指纹无实质增量，提前收敛并转人工研判。\n"
            "> 结论基于现有证据，未必完整。\n"
        )
        return banner + "\n" + report_md if report_md else banner

    # ==================== Checkpoint / Resume ====================

    def _checkpoint(
        self,
        current_stage: str,
        completed: list[str],
        artifacts: dict[str, Any],
        case_index: int = 0,
        total_cases: int = 0,
        test_id: str = "",
    ) -> None:
        """在某阶段打一个 checkpoint（workspace 未配置则静默跳过）。"""
        if not self._store:
            return
        from core.checkpoint import TestCheckpoint, capture_current_env

        now = datetime.now()
        ckpt = TestCheckpoint(
            checkpoint_id=f"ckpt_{now.strftime('%Y%m%d_%H%M%S_%f')}",
            test_id=test_id,
            plugin=self.plugin.name,
            current_stage=current_stage,
            completed_stages=list(completed),
            current_case_index=case_index,
            total_cases=total_cases,
            artifacts=artifacts,
            env_snapshot=capture_current_env(self.workspace_path or ""),
            created_at=now.isoformat(timespec="seconds"),
            workspace_path=self.workspace_path or "",
        )
        self._store.save(ckpt)

    # ---------- 中间产物落盘（让 resume 跳过阶段后仍有数据可用）----------

    def _artifact_path(self, name: str):
        """workspace 下的产物文件路径；未配 workspace 返回 None。"""
        if not self.workspace_path:
            return None
        from pathlib import Path
        return Path(self.workspace_path) / name

    def _save_raw_data(self, raw: list[RawData]) -> None:
        """执行结果落盘为 raw_data.json，供 resume 复用。"""
        p = self._artifact_path("raw_data.json")
        if p is None:
            return
        import json
        p.write_text(
            json.dumps([r.to_dict() for r in raw], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_raw_data(self) -> list[RawData]:
        """resume 时从 raw_data.json 重建执行结果；无文件/损坏则空列表。"""
        p = self._artifact_path("raw_data.json")
        if p is None or not p.exists():
            return []
        import json
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [RawData.from_dict(d) for d in data]
        except (json.JSONDecodeError, OSError, TypeError):
            return []

    def _save_diagnosis(self, diagnosis: Diagnosis | None) -> None:
        """诊断结论落盘为 diagnosis.json（含复测决策 + Golden-set 关联字段）。"""
        p = self._artifact_path("diagnosis.json")
        if p is None:
            return
        import json
        payload = None if diagnosis is None else {
            "text": diagnosis.text,
            "rounds": diagnosis.rounds,
            "tool_calls": diagnosis.tool_calls,
            "need_reproduce": diagnosis.need_reproduce,
            "reproduce_hint": diagnosis.reproduce_hint,
            "target_evidence": diagnosis.target_evidence,
            "matched_bad_cases": diagnosis.matched_bad_cases,
            "is_new_bug": diagnosis.is_new_bug,
            "bug_summary": diagnosis.bug_summary,
            # P8 置信度门槛三键
            "confidence": diagnosis.confidence,
            "evidence_sufficiency": diagnosis.evidence_sufficiency,
            "needs_human_review": diagnosis.needs_human_review,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_reproduce_round(self, round_no: int, extra_raw: list[RawData],
                              hint: str) -> None:
        """把某轮复测的独立 raw_data 单独存一份，供研发回溯。"""
        if not self.workspace_path:
            return
        from pathlib import Path
        import json
        p = Path(self.workspace_path) / f"raw_data_round{round_no}.json"
        payload = {
            "round": round_no,
            "reproduce_hint": hint,
            "raw_data": [r.to_dict() for r in extra_raw],
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_diagnosis(self) -> Diagnosis | None:
        """resume 时从 diagnosis.json 重建诊断结论。"""
        p = self._artifact_path("diagnosis.json")
        if p is None or not p.exists():
            return None
        import json
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if not d:
                return None
            return Diagnosis(
                text=d.get("text", ""),
                rounds=d.get("rounds", 0),
                tool_calls=d.get("tool_calls", []),
                need_reproduce=d.get("need_reproduce", False),
                reproduce_hint=d.get("reproduce_hint", ""),
                target_evidence=d.get("target_evidence", []) or [],
                matched_bad_cases=d.get("matched_bad_cases", []) or [],
                is_new_bug=d.get("is_new_bug", False),
                bug_summary=d.get("bug_summary", {}) or {},
                # P8：旧文件缺这三键时取安全默认，行为同升级前
                confidence=d.get("confidence") or "medium",
                evidence_sufficiency=d.get("evidence_sufficiency") or "sufficient",
                needs_human_review=d.get("needs_human_review", False),
            )
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def restore_state(self, ckpt) -> None:
        """
        从 checkpoint 重建任务状态（不恢复完整 agent 上下文，只吃结构化产物）。

        面试口径：子 agent 每次全新启动，主控吃 checkpoint 的结构化状态决策下一步。
        """
        self._restored = {
            "completed_stages": list(ckpt.completed_stages),
            "current_stage": ckpt.current_stage,
            "current_case_index": ckpt.current_case_index,
            "artifacts": dict(ckpt.artifacts or {}),
            "test_id": ckpt.test_id,
        }

    def resume(self, on_event: OnEvent = None, run_preflight: bool = True) -> TestReport:
        """
        从最新 checkpoint 恢复执行：先由 PreflightAgent 研判漂移，再接着跑。

        run_preflight=False 时跳过 LLM 研判（单测/离线用）。
        """
        if not self._store:
            raise RuntimeError("未配置 workspace，无法 resume")
        ckpt = self._store.load_latest()
        if ckpt is None:
            raise RuntimeError("未找到可用 checkpoint（可能全部损坏）")

        # 1. Agent 驱动的漂移研判
        if run_preflight:
            from core.checkpoint import PreflightAgent, DriftDecision
            if on_event:
                on_event("stage", {"stage": "测试前环境校验"})
            assessment = PreflightAgent(ckpt).assess(on_event=on_event)
            if on_event:
                on_event("preflight", assessment)
            decision = assessment["decision"]
            if decision == DriftDecision.ABORT_TO_HUMAN:
                raise RuntimeError(f"漂移研判要求转人工：{assessment['reason']}")
            # RERUN_STAGE：把当前阶段从已完成里剔除，强制重跑
            if decision == DriftDecision.RERUN_STAGE:
                for st in assessment.get("affected_stages") or [ckpt.current_stage]:
                    if st in ckpt.completed_stages:
                        ckpt.completed_stages.remove(st)

        # 2. 重建状态并接着跑
        self.restore_state(ckpt)
        requirement = (ckpt.artifacts or {}).get("requirement_raw", "")
        return self.run(requirement, on_event=on_event, _resume_ckpt=ckpt)

    def _analysis_task(self, requirement: str, raw: list[RawData]) -> str:
        """把原始需求 + 已采集证据拼成给分析 Agent 的任务描述。"""
        task = requirement
        if raw:
            hint = "；执行阶段已采集到以下采集器数据：" + ", ".join(
                sorted({r.collector for r in raw})
            )
            task = requirement + hint + "。请主动调用取证工具进一步分层定位。"
        return task
