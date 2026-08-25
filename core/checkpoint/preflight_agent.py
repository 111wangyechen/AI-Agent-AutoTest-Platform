"""
PreflightAgent · 测试前 Agent 驱动的环境漂移研判

用户诉求原文：「测试前 agent 驱动抓取设备状态，并由 agent 自己对比是否一致」。

这里的分工是本设计的关键：
- 确定性层（environment.py + drift.py）负责「抓」和「机械对比」——可单测、可复现。
- PreflightAgent（本文件）是一个真 LLM Agent，持有一组采集/对比 Tool，
  由它自主决定：抓哪些锚点、逐项对比、结合测试语义研判「这些差异要不要紧」，
  最后给出决策（继续 / 标注继续 / 重跑当前阶段 / 转人工）。

这正好落地面试稿里「resume 前的环境一致性校验由 Agent 判定」的说法，
而不是写死 if-else —— Agent 能解释每一项差异对本次测试的实际影响。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from core.tools import Tool, ToolRegistry
from core.agents.base import LLMAgent

from .models import (
    DriftDecision,
    EnvironmentSnapshot,
    TestCheckpoint,
)
from . import environment as env_mod
from .drift import detect_drift


PREFLIGHT_SYSTEM_PROMPT = """你是 AI 测试平台的「测试前环境校验官」。

一次长流程测试中断后要 resume 时，你必须先判断：**当前环境是否还和上次
checkpoint 打点时一致**。如果设备重启了、版本变了、日志被清空了、网络掉了，
却基于旧假设接着跑，测试结论就是错的。

你手上有这些工具（可多次调用、自主决定顺序）：
- get_checkpoint_env: 读 checkpoint 里存的环境快照锚点
- capture_current_env: 现在重新抓一份当前环境快照
- compare_env: 让确定性引擎逐项对比两份快照，返回命中的漂移场景和严重程度

工作方式：
1. 先取 checkpoint 锚点，再抓当前环境，然后对比。
2. 结合测试语义研判每一项差异的**实际影响**——例如：
   - 版本变了 → 旧的采集数据分析口径可能失效，通常要重跑
   - 设备重启 → 日志缓冲区清空，前面采的日志还在 workspace 里就还能用，
     但正在进行的采集要重来
   - 网络轻微抖动 → 若不涉及云端识别阶段，可标注存疑后继续
   - 配置/checkpoint 被篡改 → 不可信，必须转人工
3. 不要机械照搬严重程度，要说清楚**为什么**。

最后必须输出一个 JSON（不要额外文字）：
{
  "consistent": true/false,
  "decision": "CONTINUE" | "CONTINUE_FLAGGED" | "RERUN_STAGE" | "ABORT_TO_HUMAN",
  "reason": "一句话说清依据",
  "affected_stages": ["若需重跑，列出受影响阶段"]
}
"""


class PreflightAgent:
    """LLM 驱动的 resume 前漂移研判 Agent。"""

    def __init__(self, checkpoint: TestCheckpoint) -> None:
        self.checkpoint = checkpoint
        self._last_current: EnvironmentSnapshot | None = None
        self.agent = LLMAgent(
            name="测试前校验",
            system_prompt=PREFLIGHT_SYSTEM_PROMPT,
            registry=self._build_registry(),
            max_rounds=8,
        )

    # ---------- 把采集/对比能力包装成 Tool 给 LLM ----------

    def _build_registry(self) -> ToolRegistry:
        return ToolRegistry([
            Tool(
                name="get_checkpoint_env",
                description="读取 checkpoint 打点时存的环境快照锚点（版本/IP/uptime/hash/网络等）。",
                input_schema={"type": "object", "properties": {}, "required": []},
                func=self._tool_get_ckpt_env,
            ),
            Tool(
                name="capture_current_env",
                description="现在重新抓取一份当前设备/workspace/网络的环境快照。",
                input_schema={"type": "object", "properties": {}, "required": []},
                func=self._tool_capture_current,
            ),
            Tool(
                name="compare_env",
                description=(
                    "用确定性引擎逐项对比 checkpoint 快照与当前快照，"
                    "返回命中的漂移场景列表和三级严重程度。需先调用过 capture_current_env。"
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
                func=self._tool_compare,
            ),
        ])

    def _tool_get_ckpt_env(self) -> dict[str, Any]:
        return {"status": "success", "result": self.checkpoint.env_snapshot.to_dict()}

    def _tool_capture_current(self) -> dict[str, Any]:
        snap = env_mod.capture_current_env(self.checkpoint.workspace_path)
        # 继承 checkpoint 记录的采集文件清单，让音频损坏检测有据可比
        snap.collected_files = self.checkpoint.env_snapshot.collected_files
        self._last_current = snap
        return {"status": "success", "result": snap.to_dict()}

    def _tool_compare(self) -> dict[str, Any]:
        if self._last_current is None:
            self._last_current = env_mod.capture_current_env(self.checkpoint.workspace_path)
            self._last_current.collected_files = self.checkpoint.env_snapshot.collected_files
        result = detect_drift(self.checkpoint.env_snapshot, self._last_current)
        return {"status": "success", "result": result.to_dict(), "summary": result.summary()}

    # ---------- 对外：跑一次研判 ----------

    def assess(self, on_event: Callable[[str, Any], None] | None = None) -> dict[str, Any]:
        """
        让 Agent 自主完成「抓取→对比→研判」，返回结构化决策。

        返回：{"consistent", "decision"(DriftDecision), "reason", "affected_stages", "raw"}
        """
        task = (
            f"测试任务 [{self.checkpoint.test_id or self.checkpoint.checkpoint_id}] "
            f"用插件 {self.checkpoint.plugin}，中断在阶段「{self.checkpoint.current_stage}」"
            f"（已完成 {self.checkpoint.completed_stages}）。"
            "请判断现在能否安全 resume。"
        )
        out = self.agent.run(task, on_event=on_event)
        parsed = _extract_json(out["text"])

        decision_raw = parsed.get("decision", "ABORT_TO_HUMAN")
        try:
            decision = DriftDecision(decision_raw)
        except ValueError:
            decision = DriftDecision.ABORT_TO_HUMAN

        return {
            "consistent": bool(parsed.get("consistent", False)),
            "decision": decision,
            "reason": parsed.get("reason", "(Agent 未给出依据)"),
            "affected_stages": parsed.get("affected_stages", []),
            "rounds": out["rounds"],
            "raw": out["text"],
        }


def _extract_json(text: str) -> dict[str, Any]:
    """从模型输出抠出第一个 JSON 对象。"""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}
