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

    def __init__(self, plugin: TestPlugin, workspace_path: str | None = None) -> None:
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

    def run(self, requirement: str, on_event: OnEvent = None, _resume_ckpt=None) -> TestReport:
        """
        跑完整流水线，返回 TestReport。

        _resume_ckpt: 内部参数，resume 时传入 checkpoint，用于跳过已完成阶段、
        复用已产出的 artifact。正常调用不传。
        """
        errors: list[str] = []
        done = set(self._restored.get("completed_stages", []))  # resume 时已完成的阶段
        test_id = self._restored.get("test_id") or datetime.now().strftime("%Y%m%d_%H%M%S")

        def stage(name: str) -> None:
            if on_event:
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

                # 分析 Agent 认为需要复测且未超轮次上限 → 补采一轮
                if (diagnosis and diagnosis.need_reproduce
                        and reproduce_rounds < MAX_REPRODUCE_ROUNDS):
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
                    continue  # 用新证据重新分析

                break  # 无需复测或已达上限，退出循环

            self._save_diagnosis(diagnosis)
            done.add("analyze")
            self._checkpoint("analyze", list(done),
                             {"requirement_raw": requirement,
                              "reproduce_rounds": reproduce_rounds}, test_id=test_id)

        # 5) 报告生成
        report_md = ""
        if "report" not in done:
            stage("报告生成")
            try:
                report_md = self.report_agent.run(req, cases, raw, diagnosis, on_event=on_event)
            except Exception as e:
                errors.append(f"报告生成失败: {e}")
            done.add("report")
            self._checkpoint("report", list(done),
                             {"requirement_raw": requirement}, test_id=test_id)

        # 6) Golden-set 记录新 bug
        #
        # 分析 Agent 标记 is_new_bug=true 时，自动写入 Golden-set 供后续复用。
        if diagnosis and diagnosis.is_new_bug and diagnosis.bug_summary:
            if on_event:
                on_event("golden_set_write", {"summary": diagnosis.bug_summary})
            try:
                from core.golden_set import GoldenSet, BadCase
                gs = GoldenSet()
                summary = diagnosis.bug_summary
                case = BadCase.new(
                    title=summary.get("title", "未命名 bug"),
                    device_model=self.plugin.device_model,
                    plugin=self.plugin.name,
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
                    on_event("golden_set_written", {"case_id": case.case_id})
            except Exception as e:
                errors.append(f"Golden-set 记录失败: {e}")

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
