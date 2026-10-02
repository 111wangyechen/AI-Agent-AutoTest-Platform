"""
通用内核 · 工具抽象

一个 Tool 把「给 Claude 看的 schema」和「真正执行的 Python 函数」绑在一起。
插件通过构造 Tool 列表，把自己的采集/诊断能力注册进平台，内核对工具零感知。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class ToolRole(str, Enum):
    """
    执行阶段的工具角色（一等公民，取代早期用方法名字符串寻址工具的脆弱做法）。

    背景：曾用「工具名是否含 playback/log 子串」找工具，插件改名即静默失效；
    后改为按方法名 getattr，仍是魔法字符串。现在角色是显式枚举，
    插件通过 tool_for_role() 声明角色→工具的映射。
    """
    PLAYBACK = "playback"
    AUDIO = "audio"
    LOG = "log"


def normalize_result(result: Any) -> dict[str, Any]:
    """
    防御性归一工具返回：保证结果一定是含 `status` 键的 dict，给 Agent 一个稳定的错误面。

    - 已是含 status 的 dict            → 原样返回
    - 是 dict 但缺 status（含裸 error）→ 补 status（有 error 键判 error，否则 success）
    - 非 dict                          → 包成 {"status": "success", "result": result}
    """
    if isinstance(result, dict):
        if "status" in result:
            return result
        # 缺 status：有 error 键视为失败，否则视为成功数据
        status = "error" if result.get("error") else "success"
        return {"status": status, **result}
    return {"status": "success", "result": result}


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
