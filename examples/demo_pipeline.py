"""
演示脚本 · 完整 5 段流水线（声学）

主控 Coordinator 串行编排 5 个子 Agent：需求分析 → 用例设计 → 执行采集
→ 结果分析 → 报告生成。逐段展示 artifact，最后打印完整测试报告。

用法：
  PLATFORM_MOCK=1 python examples/demo_pipeline.py
"""

from rich.panel import Panel
from rich.markdown import Markdown

from _shared import console, preflight, make_event_printer, default_registry

REQUIREMENT = (
    "对灵犀屏做唤醒测试：在不同距离和噪声下连播100次唤醒词，"
    "要求唤醒率≥95%。如有未唤醒，请分层定位根因并出报告。"
)


def main() -> None:
    console.print(Panel.fit(
        "[bold cyan]AI Agent 测试平台 · 完整流水线演示（声学）[/bold cyan]",
        border_style="cyan",
    ))
    preflight()
    console.print(Panel(REQUIREMENT, title="测试需求", border_style="yellow"))

    from core.agents import Coordinator
    coord = Coordinator(default_registry.get("acoustic_lamp"))

    report = coord.run(REQUIREMENT, on_event=make_event_printer())

    console.print()
    console.print(f"[bold]需求解析[/bold]：设备={report.requirement.device} "
                  f"类型={report.requirement.test_type} 指标={report.requirement.metrics}")
    console.print(f"[bold]用例[/bold]：{len(report.cases)} 条  "
                  f"[bold]采集[/bold]：{len(report.raw_data)} 条原始数据")
    if report.diagnosis:
        console.print(Panel(
            Markdown(report.diagnosis.text),
            title=f"[bold green]结果分析（{report.diagnosis.rounds} 轮，"
                  f"{len(report.diagnosis.tool_calls)} 次工具调用）[/bold green]",
            border_style="green",
        ))
    if report.report_md:
        console.print(Panel(Markdown(report.report_md), title="[bold]最终测试报告[/bold]",
                            border_style="blue"))
    if report.stage_errors:
        console.print(f"[red]降级记录：{report.stage_errors}[/red]")


if __name__ == "__main__":
    main()
