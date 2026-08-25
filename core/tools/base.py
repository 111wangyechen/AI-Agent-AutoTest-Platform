"""
通用内核 · 工具抽象

一个 Tool 把「给 Claude 看的 schema」和「真正执行的 Python 函数」绑在一起。
插件通过构造 Tool 列表，把自己的采集/诊断能力注册进平台，内核对工具零感知。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Tool:
    """一个可被 Agent 调用的工具：schema + 执行函数。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., dict[str, Any]]

    def to_claude(self) -> dict[str, Any]:
        """转成 Claude Messages API 的 tools[] 元素。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def run(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """执行工具，参数不匹配时兜底为结构化错误，不让整轮循环崩掉。"""
        try:
            return self.func(**tool_input)
        except TypeError as e:
            return {"status": "error", "result": f"参数错误: {e}"}
        except Exception as e:  # 工具内部异常也要转成结果回传给 Agent
            return {"status": "error", "result": f"工具执行异常: {e}"}


def tool(name: str, description: str, input_schema: dict[str, Any] | None = None):
    """装饰器：把一个函数快速包装成 Tool（插件里写工具更省事）。"""
    schema = input_schema or {"type": "object", "properties": {}, "required": []}

    def deco(fn: Callable[..., dict[str, Any]]) -> Tool:
        return Tool(name=name, description=description, input_schema=schema, func=fn)

    return deco
