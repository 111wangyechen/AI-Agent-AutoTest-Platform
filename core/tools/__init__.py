"""通用内核 · 工具系统：Tool 抽象 + ToolRegistry。"""

from .base import Tool, ToolRole, normalize_result, tool
from .registry import ToolRegistry

__all__ = ["Tool", "ToolRole", "normalize_result", "tool", "ToolRegistry"]
