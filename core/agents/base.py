"""
通用内核 · LLM Agent 基类

把 MVP 时期 AnalysisAgent.analyze() 的「手动工具循环」提炼成通用基类：
任何子 Agent 只要给定 system prompt + 一个 ToolRegistry，就能自主调用工具、
多轮取证、直到给出结论。这是「结果分析 Agent」皇冠明珠的载体，也被
需求/用例/报告 Agent 复用（它们只是工具更少、轮数更短）。

鉴权沿用 config：官方 api_key 或公司网关 auth_token + base_url，二者皆自动生效。
"""

from __future__ import annotations

from typing import Any, Callable

import anthropic

from core.config import config
from core.tools import ToolRegistry


def _build_client() -> anthropic.Anthropic:
    """按 config 组装 Anthropic 客户端，兼容官方直连与公司网关两种鉴权。"""
    kwargs: dict[str, Any] = {}
    if config.auth_token:
        kwargs["auth_token"] = config.auth_token
    elif config.api_key:
        kwargs["api_key"] = config.api_key
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return anthropic.Anthropic(**kwargs)


class LLMAgent:
    """一个由 Claude 驱动、可自主调用工具的 Agent。"""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        registry: ToolRegistry | None = None,
        max_rounds: int = 12,
        max_tokens: int = 8000,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.registry = registry or ToolRegistry()
        self.max_rounds = max_rounds
        self.max_tokens = max_tokens
        self.client = _build_client()
        self.model = config.model

    def run(self, task: str, on_event: Callable[[str, Any], None] | None = None) -> dict[str, Any]:
        """
        执行一次任务：多轮 tool_use 循环，直到 Agent 给出结论或触顶。

        返回 {"text": 结论, "rounds": 轮数, "tool_calls": [...]}
        """
        def emit(kind: str, payload: Any) -> None:
            if on_event:
                on_event(kind, {"agent": self.name, **payload})

        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        tool_calls_log: list[dict[str, Any]] = []
        has_tools = len(self.registry) > 0
        rounds = 0

        while rounds < self.max_rounds:
            rounds += 1
            kwargs: dict[str, Any] = dict(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},  # Opus 4.8 自适应思考
                system=self.system_prompt,
                messages=messages,
            )
            if has_tools:
                kwargs["tools"] = self.registry.claude_schema()

            resp = self.client.messages.create(**kwargs)

            if has_tools and resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                tool_results = self._run_tool_uses(resp.content, tool_calls_log, emit)
                messages.append({"role": "user", "content": tool_results})
                continue

            final_text = "".join(b.text for b in resp.content if b.type == "text")
            emit("final", {"text": final_text})
            return {"text": final_text, "rounds": rounds, "tool_calls": tool_calls_log}

        return {
            "text": "达到最大工具轮数仍未得出结论，请人工介入。",
            "rounds": rounds,
            "tool_calls": tool_calls_log,
        }

    def _run_tool_uses(self, content, tool_calls_log, emit) -> list[dict[str, Any]]:
        """执行本轮所有 tool_use 块，收集结果一次性回传。"""
        results = []
        for block in content:
            if block.type != "tool_use":
                continue
            emit("tool_call", {"name": block.name, "input": dict(block.input)})
            result = self.registry.execute(block.name, dict(block.input))
            tool_calls_log.append(
                {"name": block.name, "input": dict(block.input), "result": result}
            )
            emit("tool_result", {"name": block.name, "result": result})
            results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}
            )
        return results
