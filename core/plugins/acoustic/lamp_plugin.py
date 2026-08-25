"""
AI 台灯插件（LL20）

设备特点：
1. 全量日志自动存储到 /tmp/log.txt（1MB 左右）
2. 四路音频需要实时抓取，使用 D:\录音工具\adb-fifo.exe
3. **音频抓取时独占 adb 连接** - 无法同时执行其他 adb 命令
4. 执行策略：串行 - 先播放音频并采集日志，后读取日志

测试场景说明：
- 音频在 **Windows 电脑端** 通过高保真音箱播放，让台灯麦克风接收
- 唤醒词：小白小白/灵犀灵犀，300 次，每条间隔 3s
- 识别词：唤醒词+指令词一组（间隔 1s），两组间隔 3s，循环 360 次
- 唤醒事件从日志中提取：关键字 "keyword:" / "Wakeup:" / "ncm:"
"""

from __future__ import annotations

import subprocess
import time
import os
import threading
from typing import Any

from core.config import config
from core.tools import Tool

from .base import AcousticPluginBase, ExecutionStrategy


# ========== 底层工具函数 ==========

def _run_adb(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """执行 adb 命令，配置了 device_addr 时自动加 -s 指定设备。"""
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


def _run_adb_retry(args: list[str], timeout: int = 30, retries: int = 3,
                   backoff: float = 1.5) -> tuple[int, str, str]:
    """
    带重试的 adb。无线 adb 抖一下就整轮采样失败——加 N 次重试兜住短暂网络问题。
    只对连接/超时类失败重试；命令本身返回非零（如文件不存在）直接返回，不掩盖真错。
    """
    last = (1, "", "")
    for i in range(retries):
        rc, out, err = _run_adb(args, timeout=timeout)
        if rc == 0:
            return rc, out, err
        # 判定"可重试":超时/找不到设备/adb 进程问题
        retryable = (
            rc in (124, 127)
            or "device offline" in (err + out).lower()
            or "connection" in (err + out).lower()
            or "no devices" in (err + out).lower()
        )
        last = (rc, out, err)
        if not retryable or i == retries - 1:
            return last
        time.sleep(backoff * (i + 1))
    return last


# ========== AI 台灯特定工具 ==========

# 唤醒事件日志：固件每次唤醒往这里写一行，只存唤醒事件（小、读得快）。
# 早期版本轮询 1MB 的 /tmp/log.txt 导致 cat 超时挂掉——改读这个专用文件。
WAKEUP_TRACE_LOG = "/tmp/wakeup_trace.log"


def _parse_wakeup_line(line: str) -> dict[str, Any] | None:
    """
    解析一行唤醒事件。trace.log 每次唤醒写两行（KEYWORD + WAKEUP），只计算 WAKEUP 行。
    尽量抽取结构化信息（时间戳/keyword/ncm 置信度），抽不到就退回原始行。
    """
    line = line.strip()
    if not line:
        return None
    # 【关键修复】每次唤醒写 KEYWORD + WAKEUP 两行，只算 WAKEUP，否则计数翻倍
    if "WAKEUP" not in line:
        return None
    import re
    # Unix 时间戳格式：[1787623213.318]
    ts_unix = re.search(r'\[(\d{10,11}(?:\.\d+)?)\]', line)
    # 或 ISO 格式（兼容旧日志）
    ts_iso = re.search(r'\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]?', line)
    kw = re.search(r'keyword[=:\s]+([a-zA-Z0-9 ]+?)(?:\s+ncm|\s+threshold|\s*$)', line)
    ncm = re.search(r'ncm[=:\s]+(\d+)', line)
    event: dict[str, Any] = {"raw": line}
    if ts_unix:
        event["timestamp_unix"] = float(ts_unix.group(1))
    elif ts_iso:
        event["timestamp"] = ts_iso.group(1)
    if kw:
        event["keyword"] = kw.group(1).strip()
    if ncm:
        event["ncm"] = int(ncm.group(1))
    return event


def _read_trace_events() -> list[dict[str, Any]]:
    """读 /tmp/wakeup_trace.log 全部唤醒事件（带重试）。失败返回空列表。"""
    rc, out, _ = _run_adb_retry(["shell", "cat", WAKEUP_TRACE_LOG], timeout=20)
    if rc != 0:
        return []
    events = []
    for line in out.splitlines():
        ev = _parse_wakeup_line(line)
        if ev is not None:
            events.append(ev)
    return events


def lamp_wakeup_test(
    audio_file: str,
    expected_count: int = 300,
    duration_s: int | None = None,
    poll_interval: int = 10,
    case_meta: dict[str, Any] | None = None,
    sensitivity: str = "标准",
) -> dict[str, Any]:
    """
    AI 台灯唤醒测试完整流程

    流程：
    1. 读取 /tmp/wakeup_trace.log 基线行数（设备可能残留历史事件）
    2. 在 Windows 端通过音箱播放唤醒音频，让台灯麦克风接收
    3. 播放期间每 poll_interval 秒读一次 trace.log，统计新增唤醒事件
    4. 播放完成（或达标/长时间无新增）后，用「最终行数 - 基线」算本轮唤醒数

    Args:
        audio_file: 本地音频文件路径（如 D:\\LL20\\唤醒音频-小白-300次.mp3）
        expected_count: 预期唤醒次数（默认 300）
        duration_s: 音频播放时长（秒）。None 时按 expected_count*5.5s 估算
                    （实测 300 次唤醒音频约 1594s，间隔约 5.3s）
        poll_interval: 采样间隔（秒）
        case_meta: 用例条件元数据（距离/角度/环境/声压级），透传进报告
        sensitivity: 唤醒灵敏度档位（"较低"/"标准"/"较高"），影响唤醒阈值

    Returns:
        包含唤醒率、时序数据、唤醒事件的字典
    """
    case_meta = case_meta or {}
    # 灵敏度注入 case_meta，确保全流程可追溯
    case_meta["sensitivity"] = sensitivity

    if config.mock or not config.has_device:
        return {
            "status": "success",
            "mode": "mock",
            "case_meta": case_meta,
            "summary": {
                "audio_file": audio_file,
                "expected_count": expected_count,
                "detected_count": 288,  # 模拟 96% 唤醒率（安静环境应偏高）
                "success_rate": "96.0%",
                "total_time": "1600.0s",
            },
            "wakeup_events": [
                {"raw": "mock", "timestamp": "2026-08-25 10:00:01.123", "keyword": "xiao bai xiao bai", "ncm": 905},
                {"raw": "mock", "timestamp": "2026-08-25 10:00:06.456", "keyword": "xiao bai xiao bai", "ncm": 912},
            ],
            "snapshots": [{"time": 10, "wakeup_count": 3}, {"time": 20, "wakeup_count": 7}],
            "issues": [],
        }

    # 1. 检查音频文件是否存在
    if not os.path.exists(audio_file):
        return {"status": "error", "result": f"音频文件不存在: {audio_file}"}

    # 2. 播放时长：优先用户显式传入；否则按经验值估算，兜底 30s 缓冲
    if duration_s is None:
        # 实测 300 次唤醒音频 1594s，每次约 5.3s；对其它 expected_count 线性外推
        duration_s = int(expected_count * 5.3) + 30
    total_wait = duration_s + 30  # 播放后再多等 30s，让最后一次唤醒事件写盘

    print(f"\n{'='*70}")
    print(f"AI 台灯唤醒测试")
    print(f"音频文件: {audio_file}")
    print(f"预期次数: {expected_count}")
    print(f"灵敏度档位: {sensitivity}")
    if case_meta:
        print(f"用例条件: {case_meta}")
    print(f"播放时长: {duration_s}s (总等待 {total_wait}s)")
    print(f"{'='*70}\n")

    # 3. 读 trace.log 基线：设备可能残留历史事件，本轮唤醒数 = 结束时行数 - 基线
    print(f"读取 {WAKEUP_TRACE_LOG} 基线...")
    baseline_events = _read_trace_events()
    baseline_count = len(baseline_events)
    print(f"基线唤醒事件数: {baseline_count}")

    # 4. 采样线程：每 poll_interval 秒读一次 trace.log，实时打印新增数
    snapshots: list[dict[str, Any]] = []
    stop_monitoring = threading.Event()
    monitor_state = {"last_count": baseline_count, "last_growth_time": 0.0}

    def monitor():
        while not stop_monitoring.is_set():
            stop_monitoring.wait(poll_interval)
            if stop_monitoring.is_set():
                break
            events = _read_trace_events()
            cur = len(events)
            delta = cur - baseline_count
            elapsed = time.time() - start_time
            snapshots.append({"time": int(elapsed), "wakeup_count": delta})
            if cur > monitor_state["last_count"]:
                monitor_state["last_count"] = cur
                monitor_state["last_growth_time"] = elapsed
                print(f"  [{elapsed:.0f}s] 累计新增唤醒 {delta}/{expected_count}")

    monitor_thread = threading.Thread(target=monitor, daemon=True)

    # 5. Windows 端播放音频（音箱→台灯麦克风）
    print(f"\n播放音频: {audio_file}")
    print("提示: 音箱已连音量合适 / 台灯麦克风朝向到位 / 环境符合用例要求\n")

    start_time = time.time()
    monitor_state["last_growth_time"] = 0.0
    try:
        os.startfile(audio_file)  # 非阻塞
    except Exception as e:
        return {"status": "error", "result": f"播放音频失败: {str(e)}"}
    monitor_thread.start()

    # 6. 主循环等待，同时支持提前结束：
    #    a) 达到 expected_count 且已过 20% 时长（避免误检）
    #    b) 超过 min(300s, duration_s/2) 时长内没有任何新增（音频提前结束或链路死）
    #    c) 到达总等待时长
    early_stop_reason = ""
    stall_threshold = min(300.0, duration_s / 2.0)
    while True:
        elapsed = time.time() - start_time
        if elapsed >= total_wait:
            break
        cur_delta = monitor_state["last_count"] - baseline_count
        if cur_delta >= expected_count and elapsed >= duration_s * 0.2:
            early_stop_reason = f"已采集到 {cur_delta} 次唤醒(>=预期)，提前结束"
            break
        # 停滞检测：仅在有过至少一次增长后才启用，避免开局慢启误报
        last_grow = monitor_state["last_growth_time"]
        if last_grow > 0 and elapsed - last_grow > stall_threshold:
            early_stop_reason = (
                f"距上次唤醒事件已过 {elapsed - last_grow:.0f}s (>{stall_threshold:.0f}s)，"
                "音频可能已结束或链路异常"
            )
            break
        time.sleep(2)

    stop_monitoring.set()
    monitor_thread.join(timeout=poll_interval + 2)
    total_time = time.time() - start_time

    # 7. 终态读取，用最新事件列表算最终成功率
    final_events_all = _read_trace_events()
    new_events = final_events_all[baseline_count:] if len(final_events_all) >= baseline_count else final_events_all
    detected_count = len(new_events)
    success_rate = f"{detected_count / expected_count * 100:.1f}%" if expected_count > 0 else "N/A"

    print(f"\n{'='*70}")
    print(f"测试完成！{('（' + early_stop_reason + '）') if early_stop_reason else ''}")
    print(f"  检测到: {detected_count}/{expected_count} 次唤醒")
    print(f"  成功率: {success_rate}")
    print(f"  总耗时: {total_time:.1f}s")
    print(f"{'='*70}\n")

    # 8. 潜在问题启发式分析
    issues: list[str] = []
    if detected_count < expected_count * 0.9:
        issues.append(f"唤醒率低于 90%（实际 {success_rate}）")
    ncm_values = [e["ncm"] for e in new_events if "ncm" in e]
    avg_ncm: float | None = None
    if ncm_values:
        avg_ncm = sum(ncm_values) / len(ncm_values)
        if avg_ncm < 850:
            issues.append(f"平均置信度偏低（{avg_ncm:.0f}），可能是环境噪声大或音量不足")
    if not final_events_all and baseline_count == 0:
        issues.append(f"{WAKEUP_TRACE_LOG} 全程无写入，需查唤醒模块或 adb 链路")

    return {
        "status": "success",
        "mode": "real_device",
        "case_meta": case_meta,
        "summary": {
            "audio_file": audio_file,
            "expected_count": expected_count,
            "detected_count": detected_count,
            "success_rate": success_rate,
            "total_time": f"{total_time:.1f}s",
            "avg_ncm": round(avg_ncm, 1) if avg_ncm is not None else None,
            "baseline_events": baseline_count,
            "early_stop_reason": early_stop_reason,
            "sensitivity": sensitivity,
        },
        "wakeup_events": new_events,
        "snapshots": snapshots,
        "trace_log_path": WAKEUP_TRACE_LOG,
        "issues": issues,
    }


def lamp_log_from_storage() -> dict[str, Any]:
    """
    AI 台灯日志采集：从 /tmp/log.txt 读取全量日志

    AI 台灯的日志自动存储在 /tmp/log.txt，大小约 1MB
    """
    if config.mock or not config.has_device:
        # Mock 模式：返回样本日志
        return {
            "status": "success",
            "mode": "mock",
            "result": "OVD: build at Aug 20 2026\nKW_LOGIC: wake up detected\n...",
        }

    print("从设备读取全量日志: /tmp/log.txt")
    rc, out, err = _run_adb(["shell", "cat /tmp/log.txt"], timeout=30)

    if rc != 0:
        return {"status": "error", "result": f"读取日志失败: {err or out}"}

    return {
        "status": "success",
        "mode": "real_device",
        "result": out,
        "log_size": len(out),
    }


def lamp_full_log_read(
    keyword: str = "",
    tail_lines: int = 500,
    max_bytes: int = 500_000,
) -> dict[str, Any]:
    """
    AI 台灯全量日志采集：读取 /data/log/log_serial.txt（约 5.8MB）

    该文件包含从系统启动到当前的所有日志（KW_LOGIC / audio_hw_primary /
    DeviceAccessService / LAST_IAT / NLUManager / TTSpeakManager 等 7 层检查点全覆盖），
    是分层定位方法论最重要的证据来源。

    Args:
        keyword: 逗号分隔的过滤关键字（如 'KW_LOGIC,audio_hw_primary'）；空则不过滤
        tail_lines: 最多返回末尾 N 行（默认 500，避免撑爆 Agent 上下文）
        max_bytes: 最多返回字节数（默认 500KB）

    Returns:
        matched_lines: 命中行数，result: 拼接后的日志文本，log_size: 原文件字节数
    """
    if config.mock or not config.has_device:
        sample = "\n".join([
            "[2026-08-25 10:00:01.123][D][app_client] keyword: xiao bai xiao bai ncm: 905",
            "[2026-08-25 10:00:01.150][D][audio_hw_primary] adev_open_input_stream: enter",
            "[2026-08-25 10:00:01.200][I][DeviceAccessService] audio stream opened",
        ])
        return {
            "status": "success", "mode": "mock",
            "matched_lines": 3, "result": sample, "log_size": len(sample),
        }

    log_path = "/data/log/log_serial.txt"
    # 先看文件大小，避免盲拉
    rc, size_out, _ = _run_adb_retry(["shell", f"stat -c %s {log_path}"], timeout=10)
    log_size = 0
    if rc == 0 and size_out.strip().isdigit():
        log_size = int(size_out.strip())

    # 用设备端 grep + tail 减小传输量（关键字过滤在设备端做，比拉全量到本地过滤高效得多）
    if keyword:
        # 支持多关键字（逗号分隔 → grep -E "a|b"）
        patterns = [k.strip() for k in keyword.split(",") if k.strip()]
        grep_expr = "|".join(patterns)
        shell_cmd = f"grep -E '{grep_expr}' {log_path} | tail -n {tail_lines}"
    else:
        shell_cmd = f"tail -n {tail_lines} {log_path}"

    rc, out, err = _run_adb_retry(["shell", shell_cmd], timeout=30)
    if rc != 0:
        return {"status": "error", "result": f"读取全量日志失败: {(err or out).strip()[:300]}"}

    # 兜底截断
    if len(out) > max_bytes:
        out = out[-max_bytes:]
        truncated = True
    else:
        truncated = False

    matched = out.count("\n") + (1 if out and not out.endswith("\n") else 0)
    return {
        "status": "success",
        "mode": "real_device",
        "log_path": log_path,
        "log_size": log_size,
        "keyword": keyword,
        "matched_lines": matched,
        "truncated": truncated,
        "result": out,
    }


def lamp_audio_capture_exclusive(
    duration: int = 600,
    output_dir: str = "",
    auto_transfer: bool = True,
) -> dict[str, Any]:
    """
    AI 台灯四路音频实时抓取（独占 adb 连接）

    使用录音工具 D:\\录音工具\\adb-fifo.exe 抓取四路 PCM 音频。
    **注意**：此工具运行时，其他所有 adb 连接失效。

    正确流程（按你提供的使用方法）：
    1. 在录音工具目录下运行 adb-fifo.exe connect <设备IP>
    2. 另起一个 cmd 窗口运行 Step-1.bat（独立窗口，保持运行）
    3. 再起一个 cmd 窗口运行 Step-2.bat（独立窗口，开始采集）
    4. 录音结束后，依次在三个窗口按 Ctrl+C 结束
    5. 重新连接 adb（adb connect <IP>）
    6. 音频文件从录音工具目录转移到目标位置并改名（防覆盖）

    本函数实现**半自动化**：启动三个独立窗口，提示用户手动 Ctrl+C 结束，
    等待结束后自动转移文件并重连 adb。

    Args:
        duration: 预计录音时长（秒），用于提示，实际结束由用户 Ctrl+C 控制
        output_dir: 音频文件转移目标目录（默认当前 workspace）
        auto_transfer: 录音结束后是否自动转移文件并改名

    Returns:
        包含音频文件路径的字典
    """
    if config.mock or not config.has_device:
        return {
            "status": "success",
            "mode": "mock",
            "result": "模拟录制四路音频",
            "audio_files": [],
        }

    # 录音工具路径
    tool_dir = r"D:\录音工具"
    adb_fifo = os.path.join(tool_dir, "adb-fifo.exe")
    step1_bat = os.path.join(tool_dir, "Step-1.bat")
    step2_bat = os.path.join(tool_dir, "Step-2.bat")

    # 检查工具是否存在
    if not all(os.path.exists(f) for f in [adb_fifo, step1_bat, step2_bat]):
        return {
            "status": "error",
            "result": f"录音工具不完整，请检查 {tool_dir}",
        }

    # 提取设备 IP（从 config.device_addr 提取）
    device_ip = config.device_addr.split(":")[0] if config.device_addr else ""
    if not device_ip:
        return {"status": "error", "result": "未配置设备地址"}

    print(f"\n{'='*60}")
    print(f"四路音频录制（独占 adb，预计时长 {duration}s）")
    print(f"设备 IP: {device_ip}")
    print(f"⚠️  警告：录音期间无法执行其他 adb 命令")
    print(f"{'='*60}\n")

    try:
        # 1. 在录音工具目录下运行 adb-fifo.exe connect
        print("【步骤 1/6】连接设备...")
        proc_connect = subprocess.Popen(
            [adb_fifo, "connect", device_ip],
            cwd=tool_dir,
            creationflags=subprocess.CREATE_NEW_CONSOLE,  # Windows 独立窗口
        )
        time.sleep(3)  # 等待连接建立

        # 2. 独立窗口启动 Step-1.bat
        print("【步骤 2/6】启动 Step-1.bat（独立窗口）...")
        proc_step1 = subprocess.Popen(
            ["cmd", "/c", "start", "cmd", "/k", step1_bat],
            cwd=tool_dir,
            shell=True,
        )
        time.sleep(2)

        # 3. 独立窗口启动 Step-2.bat
        print("【步骤 3/6】启动 Step-2.bat（独立窗口，开始采集）...\n")
        proc_step2 = subprocess.Popen(
            ["cmd", "/c", "start", "cmd", "/k", step2_bat],
            cwd=tool_dir,
            shell=True,
        )
        time.sleep(2)

        # 4. 提示用户手动结束
        print(f"✓ 三个窗口已启动，正在录音...")
        print(f"预计录音时长: {duration} 秒")
        print(f"\n{'─'*60}")
        print("录音结束后请按以下顺序操作：")
        print("  1. 在 Step-2.bat 窗口按 Ctrl+C 停止采集")
        print("  2. 在 Step-1.bat 窗口按 Ctrl+C 停止录音")
        print("  3. 在 adb-fifo 窗口按 Ctrl+C 断开连接")
        print(f"{'─'*60}\n")
        input("完成上述操作后，按 Enter 键继续...")

        # 5. 重连 adb
        print("\n【步骤 4/6】重新连接 adb...")
        reconnect_proc = subprocess.run(
            ["adb", "connect", config.device_addr],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if reconnect_proc.returncode == 0:
            print(f"✓ adb 已重新连接: {config.device_addr}")
        else:
            print(f"⚠️  adb 重连失败（可手动执行 adb connect）: {reconnect_proc.stderr}")

        # 6. 查找并转移音频文件
        print("\n【步骤 5/6】查找生成的 PCM 文件...")
        audio_files = []
        for f in os.listdir(tool_dir):
            if f.endswith(".pcm"):
                full_path = os.path.join(tool_dir, f)
                audio_files.append(full_path)
                print(f"  找到: {f} ({os.path.getsize(full_path) // 1024} KB)")

        if not audio_files:
            return {
                "status": "warning",
                "result": "未找到 PCM 文件，请检查录音是否正常",
            }

        # 7. 自动转移并改名（防覆盖）
        transferred = []
        if auto_transfer and output_dir:
            print(f"\n【步骤 6/6】转移音频文件到: {output_dir}")
            os.makedirs(output_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            for src in audio_files:
                basename = os.path.basename(src)
                name, ext = os.path.splitext(basename)
                new_name = f"{name}_{timestamp}{ext}"
                dst = os.path.join(output_dir, new_name)
                import shutil
                shutil.copy2(src, dst)
                transferred.append(dst)
                print(f"  转移: {basename} → {new_name}")
            print(f"✓ 已转移 {len(transferred)} 个文件")
        else:
            transferred = audio_files
            print("\n⚠️  音频文件保留在录音工具目录，下次录音会覆盖，请及时转移")

        print(f"\n{'='*60}")
        print("录音完成！")
        print(f"{'='*60}\n")

        return {
            "status": "success",
            "mode": "real_device",
            "result": f"录制完成，生成 {len(audio_files)} 个音频文件",
            "audio_files": transferred,
            "original_files": audio_files if auto_transfer else [],
        }

    except Exception as e:
        return {
            "status": "error",
            "result": f"录音过程异常: {str(e)}",
        }


# ========== AI 台灯插件 ==========

class LampPlugin(AcousticPluginBase):
    """AI 台灯（LL20）声学测试插件"""

    name = "acoustic_lamp"
    device_model = "AI台灯"
    device_desc = "AI 台灯·LL20，全量日志存储 + 四路音频实时抓取"

    def audio_playback_tool(self) -> Tool:
        """唤醒测试完整流程工具（播放音频 + 采集日志 + 统计唤醒率）"""
        return Tool(
            name="lamp_wakeup_test",
            description=(
                "AI 台灯唤醒测试完整流程：在 Windows 端播放唤醒音频，"
                "同时实时采集设备日志，统计唤醒次数和成功率"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "audio_file": {
                        "type": "string",
                        "description": "本地唤醒音频文件路径（将在 Windows 端播放）",
                    },
                    "expected_count": {
                        "type": "integer",
                        "description": "预期唤醒次数（默认 300）",
                        "default": 300,
                    },
                    "duration_s": {
                        "type": "integer",
                        "description": "音频播放时长（秒）。不传按 expected_count*5.3s 估算",
                    },
                    "poll_interval": {
                        "type": "integer",
                        "description": "trace.log 采样间隔（秒），默认 10",
                        "default": 10,
                    },
                    "case_meta": {
                        "type": "object",
                        "description": "用例条件元数据(距离/角度/环境类型/声压级)",
                    },
                    "sensitivity": {
                        "type": "string",
                        "enum": ["较低", "标准", "较高"],
                        "description": "唤醒灵敏度档位（较低/标准/较高），影响唤醒阈值",
                        "default": "标准",
                    },
                },
                "required": ["audio_file"],
            },
            func=lamp_wakeup_test,
        )

    def log_collector(self) -> Tool:
        """日志采集器：从设备存储读取"""
        return Tool(
            name="lamp_log_from_storage",
            description="从 AI 台灯设备的 /tmp/log.txt 读取全量日志",
            input_schema={"type": "object", "properties": {}},
            func=lamp_log_from_storage,
        )

    def audio_collector(self) -> Tool:
        """音频采集器：四路实时抓取（独占 adb）"""
        return Tool(
            name="lamp_audio_capture_exclusive",
            description=(
                "AI 台灯四路音频实时抓取（独占 adb 连接）。"
                "使用录音工具抓取四路 PCM 音频，期间无法执行其他 adb 命令。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "duration": {
                        "type": "integer",
                        "description": "录音时长（秒），默认 600",
                        "default": 600,
                    }
                },
            },
            func=lamp_audio_capture_exclusive,
        )

    def execution_strategy(self) -> ExecutionStrategy:
        """
        执行策略：串行 - 音频优先

        因为音频抓取独占 adb，无法并行采集日志
        流程：播放音频（并实时采集日志）→ (可选)抓取四路音频
        """
        return "sequential_audio_first"

    def analysis_tools(self) -> list[Tool]:
        """
        分析工具集：基类通用工具 + 台灯专属全量日志读取

        全量日志 /data/log/log_serial.txt 是 7 层检查点最重要的证据源，
        分析 Agent 用它做分层定位（比 logcat_capture 覆盖更长时间/更全 tag）。
        """
        base_tools = super().analysis_tools()
        return base_tools + [
            Tool(
                name="lamp_full_log_read",
                description=(
                    "读取 AI 台灯 /data/log/log_serial.txt 全量日志（约 5.8MB，从启动到当前）。"
                    "这是分层定位最重要的证据源，覆盖比 logcat_capture 更长时间、更全 tag。\n"
                    "\n"
                    "AI 台灯（LL20）实际使用的日志 tag（按 7 层检查点映射）:\n"
                    "  1. 唤醒触发:      keyword / ncm / WAKEUP / LISTEN_MSG_WAKEUP / app_client\n"
                    "  2. 音频流开启:    audio / IFarVoiceDataReceiver / DeviceAccessService\n"
                    "  3. 3A 算法:       OVD / audio_3a\n"
                    "  4. 音频采集:      IFarVoiceDataReceiver / VAD\n"
                    "  5. 云端识别:      IFLY / IAT / cloud / TimeRecorder\n"
                    "  6. 语义/TTS:      NLU / TTS / shortplayer / APP_PLAYER\n"
                    "  7. 资源释放:      DeviceAccessService / listen_msg\n"
                    "\n"
                    "用法：keyword 用逗号分隔多关键字（内部拼成 grep -E 正则）；"
                    "tail_lines 默认 500 行以控制上下文；一次抓不到用不同 keyword 迭代。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "逗号分隔的过滤关键字（如 'KW_LOGIC,audio_hw_primary'）；空则不过滤",
                        },
                        "tail_lines": {
                            "type": "integer",
                            "description": "最多返回末尾 N 行，默认 500",
                            "default": 500,
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": "最多返回字节数，默认 500000（500KB）",
                            "default": 500000,
                        },
                    },
                    "required": [],
                },
                func=lamp_full_log_read,
            ),
        ]

    # 覆盖基类：识别标准 caseN 编号并注入用例条件元数据
    # 让"case2 3m 正对 安静环境"这样的自然语言需求生成带 case_meta 的可执行用例
    def generate_cases(self, req):
        import re
        from core.plugins import TestCase

        raw = req.raw or ""
        audio_path = self._extract_audio_path(raw)
        expected = self._extract_expected_count(raw)

        # 抠出 caseN 标签
        case_match = re.search(r"case\s*(\d+)", raw, re.IGNORECASE)
        case_num = case_match.group(1) if case_match else "01"
        case_id = f"AI台灯-CASE{case_num}"

        # 抠出关键条件——保守解析，抠不到就留空，不硬猜
        meta: dict[str, Any] = {"case_id": f"case{case_num}"}
        m = re.search(r"(\d+(?:\.\d+)?)\s*m(?!m|s)", raw)
        if m:
            meta["distance_m"] = float(m.group(1))
        if "正对" in raw or "90" in raw:
            meta["angle_deg"] = 90
        elif "45" in raw:
            meta["angle_deg"] = 45
        if "安静" in raw:
            meta["environment"] = "安静环境"
        elif "家居" in raw or "客厅" in raw:
            meta["environment"] = "家居环境"
        elif "商场" in raw:
            meta["environment"] = "商场环境"
        # 声压级/信噪比
        for label, key in [("65dB", "source_spl_dba"), ("45dB", "ambient_noise_dba"), ("20dB", "snr_db")]:
            if label in raw:
                meta[key] = int(label.replace("dB", ""))
        # 唤醒词
        for kw in ["小白小白", "灵犀灵犀", "小飞小飞"]:
            if kw in raw:
                meta["wakeword"] = kw
                break

        # 灵敏度档位（默认标准）——用例条件的重要变量，必须显式记录
        sensitivity = "标准"
        for level in ["较低", "较高"]:  # "标准"作为兜底，最后判定
            if f"灵敏度{level}" in raw or f"{level}灵敏度" in raw or f"{level}档" in raw:
                sensitivity = level
                break
        meta["sensitivity"] = sensitivity

        return [TestCase(
            case_id=case_id,
            title=f"{self.device_model} case{case_num} 唤醒测试（{expected} 次·{sensitivity}档）",
            params={
                "collector": self.audio_playback_tool().name,
                "collector_args": {
                    "audio_file": audio_path,
                    "expected_count": expected,
                    "case_meta": meta,
                    "sensitivity": sensitivity,
                },
            },
            expected="唤醒率 ≥ 90%",
        )]


# ========== 注册插件 ==========

from core.plugins import default_registry

default_registry.register(LampPlugin())
