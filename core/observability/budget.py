"""
通用内核 · 运行预算闸（P5：预算管控）

对标 TestAgent-CC 的 Run Budget，做轻量单文件版：一次运行给两道硬闸——
token 总量上限 + 墙钟时限。任一越界即置「停机标志」，由 Coordinator 在
下一个 LLM 阶段启动前查到后诚实收尾（不再空烧，报告如实标注提前终止），
而不是硬崩或无人拦地跑飞。

设计原则（忠于本平台约定）：
- 默认 None = 不设限，行为与升级前完全一致（强回归保护）。
- 只做「判越界」，不负责「怎么停」——停机动作在 Coordinator，职责单一。
- 与 Tracer 共用同一份 usage：Tracer 每累加一次 LLM usage 就喂给本账本，
  一次采集两用（可观测 + 预算），不重复埋点。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class RunBudget:
    """
    单次运行的预算闸。两个上限任一越界即视为停机。

    max_total_tokens: input+output 累计上限（None=不限）。
    max_wall_clock_s: 从 start() 起的墙钟秒数上限（None=不限）。
    """

    max_total_tokens: int | None = None
    max_wall_clock_s: float | None = None

    # 运行态（非构造入参）
    _input_tokens: int = 0
    _output_tokens: int = 0
    _start_ts: float | None = None

    def start(self) -> None:
        """记录运行起始时刻（墙钟止损的计时原点）。可重复调用以重置。"""
        self._start_ts = time.monotonic()
        self._input_tokens = 0
        self._output_tokens = 0

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        """累加一次 LLM 调用的 token 消耗。"""
        self._input_tokens += max(0, int(input_tokens or 0))
        self._output_tokens += max(0, int(output_tokens or 0))

    @property
    def total_tokens(self) -> int:
        return self._input_tokens + self._output_tokens

    def elapsed_s(self) -> float:
        """自 start() 起的墙钟秒数；未 start 返回 0。"""
        if self._start_ts is None:
            return 0.0
        return time.monotonic() - self._start_ts

    def exceeded(self) -> bool:
        """任一维度越界返回 True。两个上限都为 None 时恒为 False（不设限）。"""
        return bool(self.reason())

    def reason(self) -> str:
        """
        返回首个越界维度：'token' / 'wall_clock' / ''（未越界）。

        token 优先于墙钟——token 是更硬的成本面，优先如实归因。
        """
        if self.max_total_tokens is not None and self.total_tokens >= self.max_total_tokens:
            return "token"
        if self.max_wall_clock_s is not None and self.elapsed_s() >= self.max_wall_clock_s:
            return "wall_clock"
        return ""

    def status(self) -> dict:
        """已用/上限/剩余的快照，供报告与事件展示。"""
        remaining_tokens = (
            None if self.max_total_tokens is None
            else max(0, self.max_total_tokens - self.total_tokens)
        )
        remaining_s = (
            None if self.max_wall_clock_s is None
            else max(0.0, round(self.max_wall_clock_s - self.elapsed_s(), 2))
        )
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "total_tokens": self.total_tokens,
            "max_total_tokens": self.max_total_tokens,
            "remaining_tokens": remaining_tokens,
            "elapsed_s": round(self.elapsed_s(), 2),
            "max_wall_clock_s": self.max_wall_clock_s,
            "remaining_s": remaining_s,
            "exceeded": self.exceeded(),
            "reason": self.reason(),
        }
