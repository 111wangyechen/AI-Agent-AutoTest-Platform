"""
通用内核 · 轻量 trace 收集器（P5：可观测性）

订阅现有 `on_event` 事件流，被动累积每次 LLM 调用的 token/耗时、每次工具调用的
耗时/成败，一次运行结束写出 `trace.json`，报告附「本次消耗概览」。不侵入业务逻辑：
子 Agent 只多 emit 了 usage 事件，Tracer 作为订阅者旁路收集。

与 RunBudget 的协作：Tracer 每收到一次 LLM usage，除累积外还喂给 budget；越界时
经 downstream 回放一个 `budget_exceeded` 事件（带 reason + status），让 Coordinator
在下一个阶段启动前查到停机标志、诚实收尾。一份 usage 采集，可观测 + 预算两用。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .budget import RunBudget

OnEvent = Callable[[str, Any], None] | None


@dataclass
class LLMCall:
    """一次 LLM 调用的消耗记录。"""

    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_s: float
    round: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ToolCall:
    """一次工具调用的记录。"""

    agent: str
    name: str
    duration_s: float
    ok: bool = True


@dataclass
class Tracer:
    """
    事件订阅者：累积 LLM/工具调用，产出消耗概览与 trace.json。

    budget: 可选预算账本；每次 LLM usage 累加后查越界，越界经 downstream 回放
            budget_exceeded 事件（只回放一次，避免刷屏）。
    downstream: 可选的下游事件回调（通常是 Coordinator 原本的 on_event），
            Tracer 透传并额外注入 budget_exceeded。
    """

    budget: RunBudget | None = None
    downstream: OnEvent = None
    llm_calls: list[LLMCall] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    _budget_notified: bool = False

    def on_event(self, kind: str, payload: dict) -> None:
        """订阅 LLMAgent/Coordinator 的事件流，累积可观测数据 + 驱动预算判定。"""
        if kind == "llm_call":
            self.llm_calls.append(LLMCall(
                agent=payload.get("agent", ""),
                model=payload.get("model", ""),
                input_tokens=int(payload.get("input_tokens", 0) or 0),
                output_tokens=int(payload.get("output_tokens", 0) or 0),
                duration_s=float(payload.get("duration_s", 0.0) or 0.0),
                round=int(payload.get("round", 0) or 0),
            ))
            if self.budget is not None:
                self.budget.add_usage(
                    payload.get("input_tokens", 0), payload.get("output_tokens", 0)
                )
                self._maybe_notify_budget()
        elif kind == "tool_result":
            # 工具结果事件：记录耗时与成败（duration_s/ok 由 base.py 埋点补充）
            self.tool_calls.append(ToolCall(
                agent=payload.get("agent", ""),
                name=payload.get("name", ""),
                duration_s=float(payload.get("duration_s", 0.0) or 0.0),
                ok=bool(payload.get("ok", True)),
            ))

    def _maybe_notify_budget(self) -> None:
        """预算越界时经 downstream 回放一次 budget_exceeded 事件。"""
        if self.budget is None or self._budget_notified:
            return
        if self.budget.exceeded():
            self._budget_notified = True
            if self.downstream:
                self.downstream("budget_exceeded", {
                    "reason": self.budget.reason(),
                    "status": self.budget.status(),
                })

    def summary(self) -> dict:
        """总 token/总耗时/各 agent 分项 + 工具调用统计。"""
        total_in = sum(c.input_tokens for c in self.llm_calls)
        total_out = sum(c.output_tokens for c in self.llm_calls)
        llm_duration = sum(c.duration_s for c in self.llm_calls)
        tool_duration = sum(c.duration_s for c in self.tool_calls)

        by_agent: dict[str, dict[str, Any]] = {}
        for c in self.llm_calls:
            a = by_agent.setdefault(c.agent, {
                "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "duration_s": 0.0,
            })
            a["llm_calls"] += 1
            a["input_tokens"] += c.input_tokens
            a["output_tokens"] += c.output_tokens
            a["duration_s"] = round(a["duration_s"] + c.duration_s, 3)

        s: dict[str, Any] = {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "llm_call_count": len(self.llm_calls),
            "tool_call_count": len(self.tool_calls),
            "tool_fail_count": sum(1 for c in self.tool_calls if not c.ok),
            "llm_duration_s": round(llm_duration, 3),
            "tool_duration_s": round(tool_duration, 3),
            "by_agent": by_agent,
        }
        if self.budget is not None:
            s["budget"] = self.budget.status()
        return s

    def dump(self, path: str) -> None:
        """写出 trace.json：概览 + 逐条 LLM/工具调用明细。"""
        data = {
            "summary": self.summary(),
            "llm_calls": [asdict(c) for c in self.llm_calls],
            "tool_calls": [asdict(c) for c in self.tool_calls],
        }
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
