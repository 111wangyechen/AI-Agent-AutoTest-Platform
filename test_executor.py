"""
测试执行 Agent - AI 台灯真实唤醒测试

测试流程：
  执行 Agent（播放音频 → 读日志）→ 结果分析 Agent → 报告生成 Agent
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from datetime import datetime
from core.plugins.models import TestCase, TestRequirement
from core import config

# import 插件包触发注册
import core.plugins.acoustic.lamp_plugin  # AI 台灯插件
from core.plugins import default_registry

def main():
    print("=" * 70)
    print("AI Agent 测试平台 - AI 台灯执行 Agent 测试")
    print("=" * 70)
    print()

    # 设置设备地址
    if not config.device_addr:
        config.device_addr = "10.7.187.152:5555"

    print(f"设备: AI 台灯 (LL20)")
    print(f"设备地址: {config.device_addr}")
    print(f"模型: {config.model}")
    print()

    from core.agents import Coordinator
    coord = Coordinator(default_registry.get("acoustic_lamp"))  # 使用 AI 台灯插件

    # 手动构造测试用例（跳过需求分析和用例设计）
    test_case = TestCase(
        case_id="LAMP-WAKEUP-01",
        title="AI 台灯真实唤醒测试（300 次）",
        params={
            "collector": "lamp_wakeup_test",  # AI 台灯的完整测试流程
            "collector_args": {
                "audio_file": r"D:\LL20\唤醒音频-小白-300次.mp3",
                "expected_count": 300,
            },
        },
        expected="唤醒率 ≥ 90%",
    )

    print(f"测试用例: {test_case.title}")
    print(f"音频文件: {test_case.params['collector_args']['audio_file']}")
    print(f"预期次数: {test_case.params['collector_args']['expected_count']}")
    print()

    # 事件回调
    def on_event(kind: str, payload):
        if kind == "exec":
            print(f"\n→ 执行: {payload['case']}")
            print(f"  采集器: {payload['collector']}")
        elif kind == "tool_result":
            status = payload["result"].get("status", "?")
            if status == "success":
                result = payload["result"]
                print(f"\n✓ 执行完成:")
                if "device_path" in result:
                    print(f"  设备路径: {result['device_path']}")
                if "log_size" in result:
                    print(f"  日志大小: {result['log_size']} 字节")
                if "summary" in result:
                    summary = result["summary"]
                    print(f"  检测次数: {summary.get('detected_count')}/{summary.get('expected_count')}")
                    print(f"  成功率: {summary.get('success_rate')}")
            else:
                print(f"\n✗ 执行失败: {status}")
                if "result" in payload["result"]:
                    print(f"  错误: {payload['result']['result']}")

    # 1. 执行阶段
    print("=" * 70)
    print("阶段 1: 执行测试")
    print("=" * 70)
    raw_data = coord.executor_agent.run([test_case], on_event=on_event)

    # 2. 结果分析阶段
    print("\n" + "=" * 70)
    print("阶段 2: 结果分析")
    print("=" * 70)

    analysis_task = "分析唤醒测试结果，定位任何失败的根本原因"
    if raw_data and raw_data[0].payload.get("issues"):
        analysis_task += "。执行阶段发现的问题：" + "、".join(raw_data[0].payload["issues"])

    print(f"分析任务: {analysis_task}\n")

    diagnosis = coord.analysis_agent.run(analysis_task, on_event=on_event)

    # 3. 报告生成阶段
    print("\n" + "=" * 70)
    print("阶段 3: 生成报告")
    print("=" * 70)

    req = TestRequirement(
        raw="唤醒测试",
        device="AI台灯",
        test_type="唤醒功能测试"
    )

    report_md = coord.report_agent.run(req, [test_case], raw_data, diagnosis, on_event=on_event)

    # 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"executor_test_report_{timestamp}.txt"

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=" * 70 + "\n")
        f.write("执行 Agent 测试报告\n")
        f.write("=" * 70 + "\n\n")

        f.write(f"测试用例: {test_case.title}\n")
        f.write(f"用例ID: {test_case.case_id}\n\n")

        f.write("=" * 70 + "\n")
        f.write("执行结果:\n")
        f.write("=" * 70 + "\n")
        for raw in raw_data:
            f.write(f"\n采集器: {raw.collector}\n")
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
                if "snapshots" in raw.payload:
                    f.write(f"\n  时序快照:\n")
                    for snap in raw.payload["snapshots"][:5]:  # 只保存前5个
                        f.write(f"    {snap['time']}s: {snap['wakeup_count']} 次唤醒\n")

        f.write("\n" + "=" * 70 + "\n")
        f.write("结果分析:\n")
        f.write("=" * 70 + "\n")
        f.write(f"分析轮数: {diagnosis.rounds}\n")
        f.write(f"工具调用: {len(diagnosis.tool_calls)} 次\n\n")
        f.write(diagnosis.text)

        f.write("\n\n" + "=" * 70 + "\n")
        f.write("最终报告:\n")
        f.write("=" * 70 + "\n")
        f.write(report_md)

    print("\n\n" + "=" * 70)
    print("测试完成！")
    print(f"报告已保存到: {output_file}")
    print("=" * 70)

    # 打印关键结果
    if raw_data and raw_data[0].payload.get("summary"):
        summary = raw_data[0].payload["summary"]
        print(f"\n✓ 测试结果:")
        print(f"  检测: {summary.get('detected_count')}/{summary.get('expected_count')}")
        print(f"  成功率: {summary.get('success_rate')}")
        print(f"  耗时: {summary.get('total_time')}")

if __name__ == "__main__":
    main()
