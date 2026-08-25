"""
音频播放工具 - 用于实际测试执行

功能：
1. 推送音频文件到设备
2. 通过设备播放音频
3. 同步采集测试数据
"""

import subprocess
import time
import os
from pathlib import Path
from typing import Any

from core.config import config


def _run_adb(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """执行 adb 命令"""
    cmd = ["adb"]
    if config.device_addr:
        cmd += ["-s", config.device_addr]
    cmd += args
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", "adb 未安装或不在 PATH 中"
    except subprocess.TimeoutExpired:
        return 124, "", f"adb 命令超时（>{timeout}s）"


def push_audio_file(local_path: str, device_path: str = "/sdcard/test_audio.mp3") -> dict[str, Any]:
    """推送音频文件到设备"""
    if not os.path.exists(local_path):
        return {
            "status": "error",
            "result": f"本地文件不存在: {local_path}"
        }

    # 检查文件大小，动态调整超时时间
    file_size_mb = os.path.getsize(local_path) / (1024 * 1024)
    # 每 MB 预留 10 秒，最少 30 秒
    push_timeout = max(30, int(file_size_mb * 10))

    print(f"正在推送音频文件到设备: {local_path} -> {device_path}")
    print(f"  文件大小: {file_size_mb:.1f} MB, 超时设置: {push_timeout} 秒")
    rc, out, err = _run_adb(["push", local_path, device_path], timeout=push_timeout)

    if rc == 0:
        return {
            "status": "success",
            "device_path": device_path,
            "result": f"文件已推送到 {device_path}"
        }
    else:
        return {
            "status": "error",
            "returncode": rc,
            "result": err or out
        }


def play_audio_on_device(device_path: str, wait_complete: bool = False) -> dict[str, Any]:
    """
    在设备上播放音频

    对于 AI 台灯等智能音箱设备，直接使用 aplay 命令行播放器

    Args:
        device_path: 设备上的音频文件路径
        wait_complete: 是否等待播放完成（用于短音频测试）
    """
    print(f"正在播放音频: {device_path}")

    # 方式1: 尝试使用 aplay（ALSA 播放器，适用于智能音箱）
    rc, out, err = _run_adb(["shell", f"aplay {device_path} &"], timeout=10)

    if rc != 0:
        # 方式2: 尝试使用 Android MediaPlayer
        play_cmd = f"am start -a android.intent.action.VIEW -d file://{device_path} -t audio/*"
        rc2, out2, err2 = _run_adb(["shell", play_cmd], timeout=10)

        if rc2 != 0:
            # 方式3: 尝试使用 stagefright
            rc3, out3, err3 = _run_adb(["shell", f"stagefright -a -o {device_path}"], timeout=10)
            if rc3 != 0:
                return {
                    "status": "error",
                    "result": f"播放失败:\n  aplay: {err or out}\n  am start: {err2 or out2}\n  stagefright: {err3 or out3}"
                }

    return {
        "status": "success",
        "result": f"已开始播放 {device_path}"
    }


def execute_wakeup_test(
    audio_file: str,
    expected_count: int = 300,
    capture_interval: int = 5
) -> dict[str, Any]:
    """
    执行完整的唤醒测试流程

    Args:
        audio_file: 本地音频文件路径
        expected_count: 预期唤醒次数
        capture_interval: 采集间隔（秒）

    Returns:
        测试结果字典
    """
    print("=" * 60)
    print("开始唤醒测试")
    print("=" * 60)

    # 1. 推送音频文件
    device_path = "/sdcard/test_wakeup.mp3"
    push_result = push_audio_file(audio_file, device_path)
    if push_result["status"] != "success":
        return push_result

    # 2. 清空 logcat 缓冲区
    print("清空 logcat 缓冲区...")
    _run_adb(["logcat", "-c"], timeout=5)

    # 3. 播放音频
    play_result = play_audio_on_device(device_path, wait_complete=False)
    if play_result["status"] != "success":
        return play_result

    # 4. 实时采集数据
    print(f"\n开始实时采集，每 {capture_interval} 秒检查一次...")
    start_time = time.time()
    wakeup_count = 0
    snapshots = []

    # 估算测试时长（300次唤醒，每次约2秒 = 600秒 = 10分钟）
    estimated_duration = expected_count * 2
    capture_times = max(1, estimated_duration // capture_interval)

    for i in range(capture_times):
        time.sleep(capture_interval)
        elapsed = time.time() - start_time

        # 采集 logcat 中的唤醒信号
        rc, out, _ = _run_adb(["logcat", "-d", "-s", "KW_LOGIC:I"], timeout=10)
        if rc == 0:
            # 统计唤醒次数
            current_count = out.count("wake up") + out.count("唤醒") + out.count("detected")
            if current_count > wakeup_count:
                wakeup_count = current_count
                print(f"[{elapsed:.0f}s] 检测到唤醒次数: {wakeup_count}/{expected_count}")

            snapshots.append({
                "time": elapsed,
                "wakeup_count": wakeup_count,
                "log_sample": out[-500:] if out else ""  # 保存最近的日志片段
            })

        # 提前结束条件：达到预期次数
        if wakeup_count >= expected_count:
            print(f"\n✓ 已完成所有 {expected_count} 次唤醒测试")
            break

    total_time = time.time() - start_time

    # 5. 最终采集完整日志
    print("\n采集完整测试日志...")
    rc, final_log, _ = _run_adb(["logcat", "-d"], timeout=30)

    # 6. 采集串口数据（如果可用）
    print("采集串口诊断数据...")
    serial_result = None
    if config.has_device:
        rc, uart_out, _ = _run_adb(
            ["shell", "timeout", "2", "/data/local/tmp/uart_test", "rxtx", "/dev/ttyS2"],
            timeout=5
        )
        if rc == 0:
            serial_result = uart_out

    return {
        "status": "success",
        "summary": {
            "audio_file": audio_file,
            "expected_count": expected_count,
            "detected_count": wakeup_count,
            "success_rate": f"{wakeup_count / expected_count * 100:.1f}%" if expected_count > 0 else "N/A",
            "total_time": f"{total_time:.1f}s",
        },
        "snapshots": snapshots,
        "final_log": final_log if rc == 0 else None,
        "serial_data": serial_result,
    }
