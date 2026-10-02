"""
通用内核 · LLM Agent 基类

把 MVP 时期 AnalysisAgent.analyze() 的「手动工具循环」提炼成通用基类：
任何子 Agent 只要给定 system prompt + 一个 ToolRegistry，就能自主调用工具、
多轮取证、直到给出结论。这是「结果分析 Agent」皇冠明珠的载体，也被
需求/用例/报告 Agent 复用（它们只是工具更少、轮数更短）。

鉴权沿用 config：官方 api_key 或公司网关 auth_token + base_url，二者皆自动生效。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
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


@dataclass
class FinalTool:
    """
    结论型工具：Agent 必须调用它来提交「结构化结论」。

    替代「让模型在 Markdown 末尾追加 ```json 块、再正则抠出来」的脆弱做法——
    改用 Anthropic tool-use，模型直接以 schema 约束的参数提交决策，
    input_schema 就是 JSON Schema，字段缺失/类型不符由 SDK 侧约束。
    """
    name: str
    description: str
    input_schema: dict[str, Any]
    required: bool = True   # True 时在收尾轮用 tool_choice 强制模型调用它

    def to_claude_schema(self) -> dict[str, Any]:
        """转成 Anthropic tools 数组里的一个工具定义。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class LLMAgent:
    """一个由 Claude 驱动、可自主调用工具的 Agent。"""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        registry: ToolRegistry | None = None,
        max_rounds: int = 12,
        max_tokens: int = 8000,
        final_tool: FinalTool | None = None,
        client: Any | None = None,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.registry = registry or ToolRegistry()
        self.max_rounds = max_rounds
        self.max_tokens = max_tokens
        self.final_tool = final_tool
        # client 可注入（单测传假 client 即可绕开真实 API），默认按 config 构造
        self.client = client if client is not None else _build_client()
        self.model = config.model

    def run(self, task: str, on_event: Callable[[str, Any], None] | None = None) -> dict[str, Any]:
        """
        执行一次任务：多轮 tool_use 循环，直到 Agent 给出结论或触顶。

        返回 {"text": 结论, "rounds": 轮数, "tool_calls": [...],
              "final_tool_input": 结论工具入参 dict 或 None}

        若配置了 final_tool：模型停止调用取证工具、准备收尾时，强制其调用结论工具，
        把结构化决策以工具入参形式取回（final_tool_input）。模型始终未调用结论工具
        （异常）时 final_tool_input 为 None，由上层降级处理。
        """
        def emit(kind: str, payload: Any) -> None:
            if on_event:
                on_event(kind, {"agent": self.name, **payload})

        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        tool_calls_log: list[dict[str, Any]] = []
        has_registry_tools = len(self.registry) > 0
        has_tools = has_registry_tools or self.final_tool is not None
        rounds = 0
        final_tool_input: dict[str, Any] | None = None

        while rounds < self.max_rounds:
            rounds += 1
            # 是否强制收尾：配了必填结论工具，且已无更多取证工具可自由调用
            # （最后一轮也强制，避免触顶还没拿到结构化决策）
            force_final = bool(
                self.final_tool
                and self.final_tool.required
                and (not has_registry_tools or rounds == self.max_rounds)
            )

            kwargs: dict[str, Any] = dict(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},  # Opus 4.8 自适应思考
                system=self.system_prompt,
                messages=messages,
            )
            tools = self._tools_schema()
            if tools:
                kwargs["tools"] = tools
            if force_final:
                # 强制模型这一轮必须调用结论工具
                kwargs["tool_choice"] = {"type": "tool", "name": self.final_tool.name}
                # 强制单工具时不能开 thinking（二者互斥），去掉
                kwargs.pop("thinking", None)

            _t0 = time.monotonic()
            resp = self.client.messages.create(**kwargs)
            _duration = time.monotonic() - _t0

            # P5 可观测 + 预算：每次 LLM 调用 emit 一条 usage 事件（Tracer 旁路订阅、
            # 累积 token/耗时并喂给 RunBudget）。假 client 无 usage 时安全取 0。
            _usage = getattr(resp, "usage", None)
            emit("llm_call", {
                "model": self.model,
                "input_tokens": int(getattr(_usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(_usage, "output_tokens", 0) or 0),
                "duration_s": round(_duration, 3),
                "round": rounds,
            })

            # 先看模型有没有调用结论工具（无论是否 tool_use 停止）
            captured = self._capture_final_tool(resp.content)
            if captured is not None:
                final_tool_input = captured
                emit("final_decision", {"input": final_tool_input})

            if has_tools and resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                tool_results = self._run_tool_uses(resp.content, tool_calls_log, emit)
                messages.append({"role": "user", "content": tool_results})
                # 拿到结论工具入参即可收尾，不必再等模型多说
                if final_tool_input is not None:
                    final_text = self._collect_text(resp.content)
                    emit("final", {"text": final_text})
                    return {
                        "text": final_text, "rounds": rounds,
                        "tool_calls": tool_calls_log,
                        "final_tool_input": final_tool_input,
                    }
                continue

            final_text = self._collect_text(resp.content)
            emit("final", {"text": final_text})
            return {
                "text": final_text, "rounds": rounds,
                "tool_calls": tool_calls_log,
                "final_tool_input": final_tool_input,
            }

        return {
            "text": "达到最大工具轮数仍未得出结论，请人工介入。",
            "rounds": rounds,
            "tool_calls": tool_calls_log,
            "final_tool_input": final_tool_input,
        }

    def _tools_schema(self) -> list[dict[str, Any]]:
        """合并取证工具 + 结论工具的 schema 列表。"""
        tools: list[dict[str, Any]] = []
        if len(self.registry) > 0:
            tools.extend(self.registry.claude_schema())
        if self.final_tool is not None:
            tools.append(self.final_tool.to_claude_schema())
        return tools

    def _capture_final_tool(self, content) -> dict[str, Any] | None:
        """从响应里抓结论工具的入参；模型未调用则返回 None。"""
        if self.final_tool is None:
            return None
        for block in content:
            if getattr(block, "type", None) == "tool_use" and block.name == self.final_tool.name:
                return dict(block.input)
        return None

    @staticmethod
    def _collect_text(content) -> str:
        return "".join(b.text for b in content if getattr(b, "type", None) == "text")

    def _run_tool_uses(self, content, tool_calls_log, emit) -> list[dict[str, Any]]:
        """执行本轮所有 tool_use 块，收集结果一次性回传。

        结论工具（final_tool）不在这里执行——它没有执行体，入参已被
        _capture_final_tool 取走。但仍需为它回一个 tool_result，否则协议不完整。
        """
        results = []
        for block in content:
            if block.type != "tool_use":
                continue
            # 结论工具：不执行，只回一个占位结果满足协议
            if self.final_tool is not None and block.name == self.final_tool.name:
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": "decision recorded"}
                )
                continue
            emit("tool_call", {"name": block.name, "input": dict(block.input)})
            _t0 = time.monotonic()
            result = self.registry.execute(block.name, dict(block.input))
            _duration = time.monotonic() - _t0
            tool_calls_log.append(
                {"name": block.name, "input": dict(block.input), "result": result}
            )
            # P5：工具结果事件补 duration_s/ok，供 Tracer 统计工具耗时与成败
            _ok = not (isinstance(result, dict) and result.get("status") == "error")
            emit("tool_result", {
                "name": block.name, "result": result,
                "duration_s": round(_duration, 3), "ok": _ok,
            })
            results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}
            )
        return results
