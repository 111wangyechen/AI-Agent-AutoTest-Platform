"""
演示脚本 · 插件切换（核心卖点 / money shot）

同一个 Coordinator.run() 代码，只换插件，测试域就从「声学故障定位」
切到「AI 台灯 WiFi 吞吐 + 通话互通」。内核一行不改——这就是「通用内核 + 场景插件」。

台灯的 iperf3 分析跑的是 8/19 真实数据（平均 26.5Mbps、峰值重传 88、3 秒断流）。

用法：
  PLATFORM_MOCK=1 python examples/demo_plugin_swap.py
"""

from rich.panel import Panel
from rich.markdown import Markdown

from _shared import console, preflight, make_event_printer, default_registry

# 同一套任务模板，喂给不同插件
JOBS = [
    ("acoustic_lamp", "唤醒测试连播100次部分未唤醒，分层定位是设备没醒还是链路故障，给结论。"),
    ("lamp", "AI 台灯语音交互时有卡顿，跑 WiFi 吞吐和通话互通，分层定位根因，给结论。"),
]


def run_one(plugin_name: str, task: str) -> None:
    """用指定插件跑一次「仅分析」，展示同一 run() 换域即换插件。"""
    from core.agents import Coordinator
    plugin = default_registry.get(plugin_name)
    console.print(Panel.fit(
        f"[bold]插件：{plugin_name}[/bold]  设备：{plugin.device_desc}",
        border_style="cyan",
    ))
    coord = Coordinator(plugin)
    with console.status(f"[cyan]{plugin_name} 分析中…[/cyan]", spinner="dots"):
        diag = coord.analyze_only(task, on_event=make_event_printer())
    console.print(Panel(
        Markdown(diag.text),
        title=f"[green]{plugin_name} 结论（{diag.rounds} 轮，{len(diag.tool_calls)} 次工具）[/green]",
        border_style="green",
    ))
    console.print()


def main() -> None:
    console.print(Panel.fit(
        "[bold cyan]AI Agent 测试平台 · 插件切换演示[/bold cyan]\n"
        "[dim]同一 Coordinator.run()，换插件即换测试域[/dim]",
        border_style="cyan",
    ))
    preflight()
    console.print(f"[dim]已注册插件：{default_registry.names()}[/dim]\n")
    for name, task in JOBS:
        run_one(name, task)


if __name__ == "__main__":
    main()
