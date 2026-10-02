"""
通用内核 · 5 个子 Agent

对应测试流水线的 5 个阶段。每个子 Agent 从插件拿到自己那部分能力：
  需求分析 / 用例设计 / 报告生成  = LLMAgent 单轮（无工具或少工具）
  结果分析                       = LLMAgent 多轮工具循环（皇冠明珠）
  执行                           = 确定性，不调 LLM，直接跑采集器

各子 Agent 只吃/吐 core.plugins.models 里的 artifact，与测试域解耦。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from core.plugins import (
    Diagnosis,
    RawData,
    TestCase,
    TestPlugin,
    TestRequirement,
)
from core.tools import ToolRegistry, ToolRole
from .base import FinalTool, LLMAgent

OnEvent = Callable[[str, Any], None] | None


class RequirementAgent:
    """需求分析：自然语言需求 -> 结构化 TestRequirement。"""

    def __init__(self, plugin: TestPlugin) -> None:
        self.plugin = plugin
        self.agent = LLMAgent(
            name="需求分析",
            system_prompt=plugin.requirement_prompt()
            + "\n只输出一个 JSON：{\"device\":..,\"test_type\":..,\"metrics\":[..],\"acceptance\":..}，不要多余文字。",
            max_rounds=1,
        )

    def run(self, raw: str, on_event: OnEvent = None) -> TestRequirement:
        out = self.agent.run(raw, on_event=on_event)
        data = _extract_json(out["text"])
        return TestRequirement(
            raw=raw,
            device=data.get("device", self.plugin.device_desc),
            test_type=data.get("test_type", ""),
            metrics=data.get("metrics", []),
            acceptance=data.get("acceptance", ""),
        )


class CaseAgent:
    """用例设计：TestRequirement -> 用例矩阵。用例模板来自插件。"""

    def __init__(self, plugin: TestPlugin) -> None:
        self.plugin = plugin

    def run(self, req: TestRequirement, on_event: OnEvent = None) -> list[TestCase]:
        # 用例矩阵由插件的模板确定性生成（可复现、可讲清），不烧 LLM
        cases = self.plugin.generate_cases(req)
        if on_event:
            on_event("cases", {"agent": "用例设计", "count": len(cases)})
        return cases


class ExecutorAgent:
    """执行：根据插件的执行策略，遍历用例、跑采集器，产出 RawData。不调 LLM。"""

    def __init__(self, plugin: TestPlugin) -> None:
        self.plugin = plugin
        self.registry = ToolRegistry(plugin.collectors())

    def run(self, cases: list[TestCase], on_event: OnEvent = None) -> list[RawData]:
        # 获取插件的执行策略
        strategy = getattr(self.plugin, "execution_strategy", lambda: "parallel")()

        if strategy == "parallel":
            return self._run_parallel(cases, on_event)
        elif strategy == "sequential_audio_first":
            return self._run_sequential_audio_first(cases, on_event)
        elif strategy == "sequential_log_first":
            return self._run_sequential_log_first(cases, on_event)
        else:
            # 默认策略：简单遍历
            return self._run_simple(cases, on_event)

    def reproduce(self, cases: list[TestCase], diagnosis, on_event: OnEvent = None) -> list[RawData]:
        """
        自主复测：分析 Agent 判断证据不足时，带着 reproduce_hint 回到执行阶段补采证据。

        与 run() 的区别：
        - 把 diagnosis.reproduce_hint 和 target_evidence 注入用例参数，
          让插件按需调整采集策略（如开启四路音频抓取、拉全量日志）
        - target_evidence 里含 "four_channel_audio" 时，强制开启 capture_audio

        插件若实现了 reproduce_cases()，用它定制复测用例；否则复用原用例 + 注入 hint。
        """
        target_evidence = getattr(diagnosis, "target_evidence", []) or []
        hint = getattr(diagnosis, "reproduce_hint", "")

        # 插件可自定义复测用例（如缩短次数、只测出问题的条件）
        if hasattr(self.plugin, "reproduce_cases"):
            repro_cases = self.plugin.reproduce_cases(cases, hint, target_evidence)
        else:
            # 默认：复用原用例，按 target_evidence 注入采集开关
            repro_cases = []
            for case in cases:
                new_args = dict(case.params.get("collector_args", {}))
                if "four_channel_audio" in target_evidence:
                    new_args["capture_audio"] = True
                new_args["reproduce_hint"] = hint
                new_params = dict(case.params)
                new_params["collector_args"] = new_args
                repro_cases.append(TestCase(
                    case_id=f"{case.case_id}-REPRO",
                    title=f"{case.title}（复测）",
                    params=new_params,
                    expected=case.expected,
                ))

        if on_event:
            on_event("reproduce_exec", {
                "agent": "执行", "hint": hint, "target_evidence": target_evidence,
                "case_count": len(repro_cases),
            })
        return self.run(repro_cases, on_event=on_event)

    def _run_simple(self, cases: list[TestCase], on_event: OnEvent) -> list[RawData]:
        """简单执行策略：遍历用例，逐个执行采集器"""
        raw: list[RawData] = []
        for case in cases:
            collector = case.params.get("collector") or self._default_collector()
            if collector is None:
                continue
            if on_event:
                on_event("exec", {"agent": "执行", "case": case.case_id, "collector": collector})
            payload = self.registry.execute(collector, case.params.get("collector_args", {}))
            raw.append(RawData(case_id=case.case_id, collector=collector, payload=payload))
        return raw

    def _run_parallel(self, cases: list[TestCase], on_event: OnEvent) -> list[RawData]:
        """
        并行执行策略（灵犀屏）：播放音频的同时实时采集日志和音频

        TODO: 实现真正的并行采集（需要多线程）
        当前简化为串行执行
        """
        return self._run_simple(cases, on_event)

    def _run_sequential_audio_first(self, cases: list[TestCase], on_event: OnEvent) -> list[RawData]:
        """
        串行执行策略 - 音频优先（AI 台灯）

        流程：
        1. 播放音频（audio_playback）
        2. (可选) 抓取音频（audio_collector）- 独占 adb
        3. 读取日志（log_collector）
        """
        raw: list[RawData] = []

        for case in cases:
            case_id = case.case_id
            collector_args = case.params.get("collector_args", {})

            # 步骤 1: 播放音频（台灯的播放工具本身就是「放音频+采日志+算唤醒率」全流程）
            playback_tool = self._role_tool("playback")
            if playback_tool:
                if "audio_file" not in collector_args:
                    # 显式记录而不是静默跳过——否则表现为「测试跑完但没听到声音」
                    raw.append(RawData(
                        case_id=f"{case_id}-playback",
                        collector=playback_tool,
                        payload={"status": "error",
                                 "result": "用例未提供 audio_file，跳过播放。"
                                           "请在 collector_args 里指定语料音频路径。"},
                    ))
                else:
                    if on_event:
                        on_event("exec", {"agent": "执行", "case": case_id,
                                          "collector": playback_tool})
                    # 透传除执行器自身控制键外的所有采集参数
                    # （audio_file/expected_count/duration_s/poll_interval/case_meta…）
                    _executor_keys = {"collector", "capture_audio"}
                    play_args = {k: v for k, v in collector_args.items()
                                 if k not in _executor_keys}
                    payload = self.registry.execute(playback_tool, play_args)
                    raw.append(RawData(case_id=f"{case_id}-playback",
                                       collector=playback_tool, payload=payload))

            # 步骤 2: (可选) 抓取音频 - 独占 adb 且耗时长，默认不抓
            # 需要时在 collector_args 里指定 capture_audio: true
            if collector_args.get("capture_audio"):
                audio_tool = self._role_tool("audio")
                if audio_tool:
                    if on_event:
                        on_event("exec", {"agent": "执行", "case": case_id, "collector": audio_tool})
                    # 音频抓取时长与测试时长相关（每次唤醒约 2 秒）
                    duration = collector_args.get("expected_count", 100) * 2
                    payload = self.registry.execute(audio_tool, {"duration": duration})
                    raw.append(RawData(case_id=f"{case_id}-audio", collector=audio_tool, payload=payload))

            # 步骤 3: 读取日志
            log_tool = self._role_tool("log")
            if log_tool:
                if on_event:
                    on_event("exec", {"agent": "执行", "case": case_id, "collector": log_tool})
                payload = self.registry.execute(log_tool, {})
                raw.append(RawData(case_id=f"{case_id}-log", collector=log_tool, payload=payload))

        return raw

    def _run_sequential_log_first(self, cases: list[TestCase], on_event: OnEvent) -> list[RawData]:
        """
        串行执行策略 - 日志优先

        流程：先读日志，再抓音频
        """
        # TODO: 实现日志优先策略（如果有设备需要）
        return self._run_simple(cases, on_event)

    # ---------- 按角色取工具（不靠名字猜） ----------
    #
    # 曾经这里用「工具名里是否含 playback/log 子串」来找工具，结果台灯插件把播放
    # 工具改名成 lamp_wakeup_test 后匹配不上，执行阶段静默跳过播放、只读日志
    # ——表现就是「测试跑完了但没听到声音」。现在角色是显式枚举 ToolRole，
    # 执行 Agent 只问 plugin.tool_for_role(role)，不再散落方法名字符串。

    def _role_tool(self, role: str | ToolRole) -> str | None:
        """
        取某个角色对应的工具名。role 接受 ToolRole 或其字符串值（playback/audio/log）。
        插件未声明该角色（或返回 None）时返回 None。
        """
        try:
            role_enum = role if isinstance(role, ToolRole) else ToolRole(role)
        except ValueError:
            return None  # 未知角色字符串
        tool = self.plugin.tool_for_role(role_enum)
        return tool.name if tool is not None else None

    def _default_collector(self) -> str | None:
        names = self.registry.names()
        return names[0] if names else None


# 分析 Agent 的复测 + 记忆库决策 schema（强制工具调用的入参约束）。
# 取代旧的「在 Markdown 末尾追加 ```json 块再正则抠」，改由模型直接以
# 结构化工具入参提交，字段/类型由 schema 约束，杜绝解析脆弱性。
DECISION_TOOL_NAME = "submit_diagnosis_decision"

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "need_reproduce": {
            "type": "boolean",
            "description": "证据不足以定论、需回到执行阶段复现并补采证据时设 true",
        },
        "reproduce_hint": {
            "type": "string",
            "description": "复现指导，如「连续播放 20 次唤醒音频，同时抓四路音频和全量串口日志」",
        },
        "target_evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "需补采的证据类型，可选 four_channel_audio / full_serial_log / "
                           "wakeup_trace / serial_link_check / sensitivity_state",
        },
        "matched_bad_cases": {
            "type": "array",
            "items": {"type": "string"},
            "description": "从 Golden-set 查到并确认命中的历史 bad-case id 列表",
        },
        "is_new_bug": {
            "type": "boolean",
            "description": "是否是应写入 Golden-set 的新 bug（唤醒率不达标 + 定位到应用/框架层通常是）",
        },
        "bug_summary": {
            "type": "object",
            "description": "若 is_new_bug=true，给出 {title, symptom, root_cause, layer, "
                           "tags[], log_signatures[], severity, fix_suggestion}",
        },
        # ---------- 证据置信度门槛（P8：防假绿）----------
        # 这两维让「模型给了结论」与「结论可不可信」解耦：低置信/证据不足的结论
        # 不被 Coordinator 当定论自动写库，而是进待人工复核队列（见 coordinator._gate_confidence）。
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "对根因判断的置信度。证据链完整且指向单一根因=high；"
                           "有合理推断但仍存在其他可能=medium；主要靠猜、证据零散=low",
        },
        "evidence_sufficiency": {
            "type": "string",
            "enum": ["sufficient", "insufficient"],
            "description": "现有证据是否足以支撑该结论。不足时应同时置 need_reproduce=true 去补采",
        },
    },
    "required": ["need_reproduce", "is_new_bug"],
}

DECISION_TOOL_INSTRUCTION = (
    "\n\n【重要】完成分层定位后，你必须调用 `submit_diagnosis_decision` 工具，"
    "以结构化参数提交复测决策与记忆库判定（need_reproduce / reproduce_hint / "
    "target_evidence / matched_bad_cases / is_new_bug / bug_summary）。"
    "同时必须给出 confidence 与 evidence_sufficiency 两维：证据不足时置信度不得填 high，"
    "且应把 evidence_sufficiency 置为 insufficient 并一并置 need_reproduce=true。"
    "宁可如实报告低置信、也不要伪装成定论。"
    "文字结论照常给出，但决策一律通过该工具提交，不要在正文里写 JSON。"
)


class AnalysisAgent:
    """结果分析（核心）：多轮工具循环做分层故障定位，产出 Diagnosis。"""

    def __init__(self, plugin: TestPlugin, client: Any | None = None) -> None:
        self.plugin = plugin
        self.agent = LLMAgent(
            name="结果分析",
            system_prompt=plugin.analysis_prompt() + DECISION_TOOL_INSTRUCTION,
            registry=ToolRegistry(plugin.analysis_tools()),
            max_rounds=12,
            final_tool=FinalTool(
                name=DECISION_TOOL_NAME,
                description="提交分层定位后的复测决策与记忆库判定。",
                input_schema=DECISION_SCHEMA,
            ),
            client=client,
        )

    def run(self, task: str, on_event: OnEvent = None) -> Diagnosis:
        out = self.agent.run(task, on_event=on_event)
        decision = out.get("final_tool_input")
        if not decision:
            # 模型未调用结论工具（异常/降级）：优雅退化，保留文字结论，决策取安全默认。
            # 兼容兜底：若正文里恰好还带了旧式 json 块，尝试抠出来（不作为主路径）。
            decision = _extract_tail_json(out["text"])
        return Diagnosis(
            text=out["text"],
            rounds=out["rounds"],
            tool_calls=out["tool_calls"],
            need_reproduce=bool(decision.get("need_reproduce", False)),
            reproduce_hint=decision.get("reproduce_hint", ""),
            target_evidence=decision.get("target_evidence", []) or [],
            matched_bad_cases=decision.get("matched_bad_cases", []) or [],
            is_new_bug=bool(decision.get("is_new_bug", False)),
            bug_summary=decision.get("bug_summary", {}) or {},
            # P8：置信度两维，缺失（旧模型/降级）取安全默认，行为同升级前
            confidence=decision.get("confidence") or "medium",
            evidence_sufficiency=decision.get("evidence_sufficiency") or "sufficient",
        )


def _extract_tail_json(text: str) -> dict[str, Any]:
    """
    [DEPRECATED] 从 Markdown 末尾抠出 ```json ... ``` 代码块。

    P0-A 起，复测/记忆库决策改由 FinalTool 强制工具调用提交（见 AnalysisAgent），
    不再依赖本函数。仅在模型异常未调用结论工具时作兜底，勿作为主路径。
    找不到或解析失败返回空 dict。
    """
    import re
    # 匹配最后一个 ```json 块
    matches = list(re.finditer(r"```json\s*\n(.*?)\n```", text, re.DOTALL))
    if matches:
        try:
            return json.loads(matches[-1].group(1))
        except json.JSONDecodeError:
            pass
    # 兜底：尝试从最后一个 { ... } 抠 JSON
    end = text.rfind("}")
    if end > 0:
        # 从后往前找匹配的 {
        depth = 0
        for i in range(end, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:end + 1])
                    except json.JSONDecodeError:
                        break
    return {}


class ReportAgent:
    """报告生成：把需求/用例/原始数据/诊断汇总成 Markdown 报告。"""

    def __init__(self, plugin: TestPlugin) -> None:
        self.plugin = plugin
        self.agent = LLMAgent(
            name="报告生成", system_prompt=plugin.report_template(), max_rounds=1
        )

    def run(
        self,
        req: TestRequirement,
        cases: list[TestCase],
        raw: list[RawData],
        diagnosis: Diagnosis | None,
        on_event: OnEvent = None,
    ) -> str:
        summary = {
            "device": req.device,
            "test_type": req.test_type,
            "case_count": len(cases),
            "collectors_used": sorted({r.collector for r in raw}),
            "diagnosis": diagnosis.text if diagnosis else "(无)",
        }
        # P8：把置信度门槛结果透给报告，低置信/证据不足时让报告如实写明，不洗成定论
        if diagnosis is not None:
            summary["confidence"] = diagnosis.confidence
            summary["evidence_sufficiency"] = diagnosis.evidence_sufficiency
            summary["needs_human_review"] = diagnosis.needs_human_review
            if diagnosis.needs_human_review:
                summary["review_notice"] = (
                    "本次结论为低置信或证据不足，请在报告显著位置标注「需人工复核」，不得当作定论。"
                )
        task = "根据以下测试过程数据生成一份结构化测试报告：\n" + json.dumps(
            summary, ensure_ascii=False, indent=2
        )
        out = self.agent.run(task, on_event=on_event)
        return out["text"]


def _extract_json(text: str) -> dict[str, Any]:
    """从模型输出里抠出第一个 JSON 对象，容错处理围栏/多余文字。"""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}
