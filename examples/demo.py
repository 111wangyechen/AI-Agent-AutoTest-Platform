"""
演示脚本 · 结果分析 Agent（核心价值，便宜快）

只跑主控里的「结果分析 Agent」，等价于 MVP 时期的行为——一次 LLM 会话、
多轮自主工具循环、分层定位声学故障。经 Coordinator + AcousticPlugin 走通用内核。

用法：
  # 在家先验证分析质量（不连设备，用样本日志）
  PLATFORM_MOCK=1 python examples/demo.py

  # 实验室连真机（无线 adb）
  DEVICE_ADDR=192.168.211.5:5555 python examples/demo.py
"""

from rich.panel import Panel
from rich.markdown import Markdown

from _shared import console, preflight, make_event_printer, default_registry

TASK = (
    "对灵犀屏做唤醒测试时，一口气播放100次唤醒词，日志显示部分未唤醒，"
    "但不确定是设备真没醒还是链路故障。请分层定位根本原因，给出结论。"
)


def main() -> None:
    console.print(Panel.fit(
        "[bold cyan]AI Agent 测试平台 · 结果分析 Agent 演示[/bold cyan]",
        border_style="cyan",
    ))
    preflight()
    console.print(Panel(TASK, title="排查任务", border_style="yellow"))

    from core.agents import Coordinator
    coord = Coordinator(default_registry.get("acoustic_lamp"))

    with console.status("[cyan]Agent 正在排查…[/cyan]", spinner="dots"):
        diag = coord.analyze_only(TASK, on_event=make_event_printer())

    console.print()
    console.print(Panel(
        Markdown(diag.text),
        title=f"[bold green]分析结论（共 {diag.rounds} 轮，{len(diag.tool_calls)} 次工具调用）[/bold green]",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
