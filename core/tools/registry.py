"""
通用内核 · 工具注册表

泛化自 MVP 时期 tools.py 里的 DISPATCH + CLAUDE_TOOLS + execute。
每个 Agent 持有一个 ToolRegistry，决定它能看见/调用哪些工具——这也是
子 Agent「工具集隔离」的落点：执行 Agent 和分析 Agent 拿到的是不同的注册表。
"""

from __future__ import annotations

from typing import Any

from .base import Tool, normalize_result


class ToolRegistry:
    """一组工具的集合：给 Claude 出 schema，按名字派发执行。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        """注册一个工具，重名直接覆盖（方便插件替换内置工具）。"""
        self._tools[tool.name] = tool

    def claude_schema(self) -> list[dict[str, Any]]:
        """给 messages.create(tools=...) 的定义列表。"""
        return [t.to_claude() for t in self._tools.values()]

    def execute(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """按名字执行工具，未知工具返回结构化错误。结果经 normalize_result 归一，
        保证一定含 status 键，给 Agent 稳定的错误面。"""
        tool = self._tools.get(name)
        if tool is None:
            return {"status": "error", "result": f"未知工具: {name}"}
        return normalize_result(tool.run(tool_input))

    def names(self) -> list[str]:
        """当前注册的工具名，便于调试和展示。"""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)
