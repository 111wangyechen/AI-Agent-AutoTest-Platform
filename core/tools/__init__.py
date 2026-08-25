"""通用内核 · 工具系统：Tool 抽象 + ToolRegistry。"""

from .base import Tool, tool
from .registry import ToolRegistry

__all__ = ["Tool", "tool", "ToolRegistry"]
