"""
通用内核 · 可观测性 + 预算管控（P5）

- Tracer：订阅 on_event 事件流，累积 token/耗时/工具调用，产出 trace.json 与消耗概览。
- RunBudget：token 硬闸 + 墙钟止损，越界置停机标志供 Coordinator 诚实收尾。

二者共用同一份 usage 采集：Tracer 收到 LLM usage 既累积可观测数据、又喂给 budget。
"""

from __future__ import annotations

from .budget import RunBudget
from .tracer import LLMCall, ToolCall, Tracer

__all__ = ["Tracer", "LLMCall", "ToolCall", "RunBudget"]
