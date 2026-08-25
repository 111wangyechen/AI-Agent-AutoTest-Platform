#!/usr/bin/env python3
"""
真实唤醒测试脚本 - 播放音频并采集数据

使用方法：
    python test_wakeup_real.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from datetime import datetime
from core.plugins.acoustic.audio_playback import execute_wakeup_test

# 配置
AUDIO_FILE = r"D:\LL20\唤醒音频-小白-300次.mp3"
EXPECTED_COUNT = 300
DEVICE_ADDR = "10.7.187.152:5555"

def main():
    # 设置设备地址
    from core import config
    config.device_addr = DEVICE_ADDR

    print("=" * 70)
    print("AI Agent 测试平台 - 真实唤醒测试")
    print("=" * 70)
    print(f"音频文件: {AUDIO_FILE}")
    print(f"预期唤醒次数: {EXPECTED_COUNT}")
    print(f"设备地址: {DEVICE_ADDR}")
    print("=" * 70)
    print()

    # 执行测试
    result = execute_wakeup_test(
        audio_file=AUDIO_FILE,
        expected_count=EXPECTED_COUNT,
        capture_interval=10  # 每10秒检查一次
    )

    # 保存结果
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"wakeup_test_result_{timestamp}.txt"

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=" * 70 + "\n")
        f.write("唤醒测试结果\n")
        f.write("=" * 70 + "\n\n")

        if result["status"] == "success":
            summary = result["summary"]
            f.write("测试摘要:\n")
            f.write(f"  音频文件: {summary['audio_file']}\n")
            f.write(f"  预期次数: {summary['expected_count']}\n")
            f.write(f"  检测次数: {summary['detected_count']}\n")
            f.write(f"  成功率: {summary['success_rate']}\n")
            f.write(f"  总耗时: {summary['total_time']}\n\n")

            f.write("采集快照:\n")
            for i, snap in enumerate(result["snapshots"], 1):
                f.write(f"  [{i}] {snap['time']:.0f}s - 唤醒 {snap['wakeup_count']} 次\n")

            f.write("\n" + "=" * 70 + "\n")
            f.write("完整日志:\n")
            f.write("=" * 70 + "\n")
            if result.get("final_log"):
                f.write(result["final_log"])

            if result.get("serial_data"):
                f.write("\n" + "=" * 70 + "\n")
                f.write("串口数据:\n")
                f.write("=" * 70 + "\n")
                f.write(result["serial_data"])
        else:
            f.write(f"测试失败: {result.get('result', 'Unknown error')}\n")

    print("\n" + "=" * 70)
    print("测试完成！")
    if result["status"] == "success":
        summary = result["summary"]
        print(f"  检测次数: {summary['detected_count']}/{summary['expected_count']}")
        print(f"  成功率: {summary['success_rate']}")
        print(f"  总耗时: {summary['total_time']}")
    print(f"  详细结果已保存到: {output_file}")
    print("=" * 70)

if __name__ == "__main__":
    main()
