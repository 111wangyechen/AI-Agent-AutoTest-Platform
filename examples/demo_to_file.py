"""
演示脚本 · 结果分析 Agent（输出到文件避免编码问题）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from _shared import preflight, default_registry

TASK = (
    "对灵犀屏做唤醒测试时，一口气播放100次唤醒词，日志显示部分未唤醒，"
    "但不确定是设备真没醒还是链路故障。请分层定位根本原因，给出结论。"
)

def main() -> None:
    print("=" * 60)
    print("AI Agent 测试平台 · 结果分析 Agent 演示")
    print("=" * 60)
    preflight()
    print(f"\n任务: {TASK}\n")
    print("Agent 正在分析...\n")

    from core.agents import Coordinator
    coord = Coordinator(default_registry.get("acoustic_lamp"))

    tool_calls = []
    def on_event(kind: str, payload) -> None:
        if kind == "tool_call":
            tool_calls.append(f"  → {payload['name']}: {payload['input']}")
            print(f"  → 调用工具 {payload['name']}")
        elif kind == "tool_result":
            status = payload["result"].get("status", "?")
            print(f"    ← {status}")

    diag = coord.analyze_only(TASK, on_event=on_event)

    # 保存到文件
    output_file = f"analysis_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write("AI Agent 测试平台 · 分析结果\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"任务: {TASK}\n\n")
        f.write(f"分析轮数: {diag.rounds}\n")
        f.write(f"工具调用: {len(diag.tool_calls)} 次\n\n")
        f.write("工具调用记录:\n")
        for call in tool_calls:
            f.write(call + "\n")
        f.write("\n" + "=" * 60 + "\n")
        f.write("分析结论:\n")
        f.write("=" * 60 + "\n\n")
        f.write(diag.text)

    print(f"\n\n{'=' * 60}")
    print(f"分析完成！")
    print(f"  轮数: {diag.rounds}")
    print(f"  工具调用: {len(diag.tool_calls)} 次")
    print(f"  结果已保存到: {output_file}")
    print("=" * 60)

if __name__ == "__main__":
    main()
