"""
完整唤醒测试演示 - 走完整的 5 阶段流水线

需求分析 → 用例设计 → 执行（播放音频+采集）→ 结果分析 → 报告生成
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from _shared import preflight, default_registry

REQUIREMENT = """
对 AI 台灯设备执行唤醒测试，使用音频文件播放 300 次唤醒词，
统计唤醒成功率，并分析任何失败的原因。
音频文件路径：D:\\LL20\\唤醒音频-小白-300次.mp3
"""

def main():
    print("=" * 70)
    print("AI Agent 测试平台 - 完整唤醒测试流程")
    print("=" * 70)
    print()
    print("测试需求:")
    print(REQUIREMENT)
    print()
    print("=" * 70)
    print()

    preflight()

    from core.agents import Coordinator

    coord = Coordinator(default_registry.get("acoustic_lamp"))

    # 事件回调
    def on_event(kind: str, payload):
        if kind == "stage":
            print(f"\n{'='*70}")
            print(f"阶段: {payload['stage']}")
            print('='*70)
        elif kind == "tool_call":
            print(f"  → 调用工具: {payload['name']}")
            if payload['name'] == 'wakeup_test_executor':
                print(f"     音频: {payload['input'].get('audio_file', 'N/A')}")
                print(f"     预期次数: {payload['input'].get('expected_count', 'N/A')}")
        elif kind == "tool_result":
            status = payload["result"].get("status", "?")
            print(f"    ← {status}")
            # 如果是测试执行结果，打印摘要
            if "summary" in payload["result"]:
                summary = payload["result"]["summary"]
                print(f"       检测: {summary.get('detected_count')}/{summary.get('expected_count')}")
                print(f"       成功率: {summary.get('success_rate')}")
                print(f"       耗时: {summary.get('total_time')}")
        elif kind == "cases":
            print(f"  → 生成 {payload['count']} 条测试用例")
        elif kind == "exec":
            print(f"  → 执行用例 {payload['case']} 使用 {payload['collector']}")

    # 执行完整流程
    print("开始执行测试...\n")
    report = coord.run(REQUIREMENT, on_event=on_event)

    # 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"full_test_report_{timestamp}.txt"

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=" * 70 + "\n")
        f.write("AI Agent 测试平台 - 完整测试报告\n")
        f.write("=" * 70 + "\n\n")

        f.write("需求:\n")
        f.write(REQUIREMENT + "\n\n")

        f.write("=" * 70 + "\n")
        f.write(f"插件: {report.plugin}\n")
        f.write(f"设备: {report.requirement.device}\n")
        f.write(f"测试类型: {report.requirement.test_type}\n")
        f.write(f"用例数: {len(report.cases)}\n")
        f.write(f"采集数据: {len(report.raw_data)} 条\n\n")

        if report.stage_errors:
            f.write("流程错误:\n")
            for err in report.stage_errors:
                f.write(f"  - {err}\n")
            f.write("\n")

        f.write("=" * 70 + "\n")
        f.write("测试用例:\n")
        f.write("=" * 70 + "\n")
        for case in report.cases:
            f.write(f"\n[{case.case_id}] {case.title}\n")
            f.write(f"  预期: {case.expected}\n")
            f.write(f"  参数: {case.params}\n")

        f.write("\n" + "=" * 70 + "\n")
        f.write("采集数据:\n")
        f.write("=" * 70 + "\n")
        for raw in report.raw_data:
            f.write(f"\n[{raw.case_id}] 采集器: {raw.collector}\n")
            if isinstance(raw.payload, dict):
                if "summary" in raw.payload:
                    summary = raw.payload["summary"]
                    f.write(f"  检测次数: {summary.get('detected_count')}/{summary.get('expected_count')}\n")
                    f.write(f"  成功率: {summary.get('success_rate')}\n")
                    f.write(f"  耗时: {summary.get('total_time')}\n")
                if "issues" in raw.payload and raw.payload["issues"]:
                    f.write(f"  发现问题:\n")
                    for issue in raw.payload["issues"]:
                        f.write(f"    - {issue}\n")

        if report.diagnosis:
            f.write("\n" + "=" * 70 + "\n")
            f.write("结果分析:\n")
            f.write("=" * 70 + "\n")
            f.write(f"分析轮数: {report.diagnosis.rounds}\n")
            f.write(f"工具调用: {len(report.diagnosis.tool_calls)} 次\n\n")
            f.write(report.diagnosis.text)

        if report.report_md:
            f.write("\n\n" + "=" * 70 + "\n")
            f.write("生成报告:\n")
            f.write("=" * 70 + "\n")
            f.write(report.report_md)

    print("\n\n" + "=" * 70)
    print("测试完成！")
    print(f"报告已保存到: {output_file}")
    print("=" * 70)

    # 打印关键结果
    if report.raw_data:
        print("\n关键结果:")
        for raw in report.raw_data:
            if isinstance(raw.payload, dict) and "summary" in raw.payload:
                summary = raw.payload["summary"]
                print(f"  检测: {summary.get('detected_count')}/{summary.get('expected_count')}")
                print(f"  成功率: {summary.get('success_rate')}")

if __name__ == "__main__":
    main()
