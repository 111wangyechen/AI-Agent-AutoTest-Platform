"""
声学插件 · 共享工具

分为两类：
1. 通用分析工具（shared_tools）：用于结果分析 Agent，跨设备通用
   - adb_tool, logcat_capture, serial_diagnose

2. 设备特定采集器：在各设备插件中实现
   - lamp_plugin.py 实现 AI 台灯的采集器
   - lingxi_plugin.py 实现灵犀屏的采集器
   - unicom_plugin.py 实现联通中屏的采集器
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from core.config import config
from core.tools import Tool


# ---------- 底层：跑一条 adb 命令 ----------

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


# ---------- 工具 1：adb ----------

def adb_tool(command: str) -> dict[str, Any]:
    """执行任意 adb 命令（不含开头的 'adb'），如 'shell getprop ro.product.model'。"""
    if config.mock or not config.has_device:
        return {
            "status": "success",
            "result": "[mock] AIUniBOX-M12 / Android 11",
            "note": "mock 模式未真正执行 adb",
        }
    rc, out, err = _run_adb(command.split())
    if rc == 0:
        return {"status": "success", "result": out.strip()}
    return {"status": "error", "returncode": rc, "result": (err or out).strip()}


# ---------- 工具 2：logcat 抓取 + tag 过滤 ----------

_MOCK_LOGCAT = """\
08-21 10:23:15.234  1234  5678 I KW_LOGIC: wake up signal detected, score=0.82
08-21 10:23:15.456  1234  5678 D audio_hw_primary: adev_open_input_stream: enter
08-21 10:23:15.567  1234  5678 E audio_hw_primary: open pcm failed: Device or resource busy
08-21 10:23:15.678  1234  5678 E DeviceAccessService: Failed to open audio stream, retry 1/3
08-21 10:23:15.789  1234  5678 W AudioFlinger: input stream already in use by pid 2020
08-21 10:23:16.001  1234  5678 E DeviceAccessService: audio stream open give up after 3 retries
"""


def logcat_capture(tags: str = "", lines: int | None = None) -> dict[str, Any]:
    """抓取一段 logcat（dump 当前缓冲区，非阻塞），按逗号分隔关键字过滤。"""
    limit = lines or config.logcat_lines
    if config.mock or not config.has_device:
        raw = _MOCK_LOGCAT
    else:
        rc, out, err = _run_adb(["logcat", "-d", "-t", str(limit)], timeout=40)
        if rc != 0:
            return {"status": "error", "returncode": rc, "result": (err or out).strip()}
        raw = out

    keywords = [t.strip() for t in tags.split(",") if t.strip()]
    result_lines = [
        line for line in raw.splitlines()
        if not keywords or any(k in line for k in keywords)
    ][-limit:]
    return {
        "status": "success",
        "matched_lines": len(result_lines),
        "result": "\n".join(result_lines),
    }


# ---------- 工具 3：串口链路健康判据 ----------

_SERIAL_SAMPLES = {
    "wakeup": {
        "link_alive": True, "rx_bytes": 32,
        "evidence": "RX [32]: <wake tag present>",
        "verdict": "链路正常，硬件层收到唤醒事件",
    },
    "audio": {
        "link_alive": True, "rx_bytes": 0,
        "evidence": "无 RX 行 / read 阻塞超时",
        "verdict": "音频链路无数据，疑似麦克风被占用（非串口死）",
    },
    "network": {
        "link_alive": True, "rx_bytes": 16,
        "evidence": "RX [16]: <heartbeat>",
        "verdict": "串口心跳正常，网络问题在协议栈以上",
    },
}


def serial_diagnose(check_type: str = "wakeup") -> dict[str, Any]:
    """串口硬件层诊断：判据是 uart_test 输出 'RX [N]' 里的字节数 N 是否 > 0。"""
    if config.mock or not config.has_device:
        r = _SERIAL_SAMPLES.get(check_type, {"verdict": "未知 check_type"})
        return {"status": "success", "result": r, "note": "mock/无设备，返回样本判据"}

    push_rc, _, _ = _run_adb(["shell", "ls", "/data/local/tmp/uart_test"], timeout=10)
    if push_rc != 0:
        return {
            "status": "error",
            "result": "设备上未找到 /data/local/tmp/uart_test，请先 adb push uart_test 到该路径",
        }
    rc, out, err = _run_adb(
        ["shell", "timeout", "5", "/data/local/tmp/uart_test", "rxtx", "/dev/ttyS2"],
        timeout=15,
    )
    rx_bytes = 0
    for m in re.finditer(r"RX \[(\d+)\]", out):
        rx_bytes = max(rx_bytes, int(m.group(1)))
    link_alive = rx_bytes > 0
    return {
        "status": "success",
        "result": {
            "link_alive": link_alive,
            "rx_bytes": rx_bytes,
            "evidence": out.strip()[:500] or (err or "").strip()[:500],
            "verdict": "链路正常" if link_alive else "链路死（收不到字节），交硬件排查",
        },
    }


# ---------- 包装成通用内核的 Tool ----------

ADB_TOOL = Tool(
    name="adb_tool",
    description=(
        "通过无线 adb 在被测设备上执行 adb 命令，用于查看设备信息、进程、属性等"
        "应用/框架层状态。当需要确认某个进程是否存在、某个属性值、或执行 shell 命令时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "adb 命令（不含开头的 adb），如 'shell ps -A | grep audio'",
            }
        },
        "required": ["command"],
    },
    func=adb_tool,
)

LOGCAT_TOOL = Tool(
    name="logcat_capture",
    description=(
        "抓取被测设备当前的 logcat 缓冲并按关键字过滤，用于获取故障发生时的日志证据。"
        "定位声学链路问题时，按检查点 tag 过滤（如 KW_LOGIC、audio_hw_primary、"
        "DeviceAccessService、LAST_IAT）来判断执行走到了哪一层、在哪层报错。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "tags": {"type": "string", "description": "逗号分隔的过滤关键字；留空返回全部"},
            "lines": {"type": "integer", "description": "最多返回的行数，默认 800"},
        },
        "required": [],
    },
    func=logcat_capture,
)

SERIAL_TOOL = Tool(
    name="serial_diagnose",
    description=(
        "串口硬件层诊断，判断物理链路死活。判据是 uart_test 输出 'RX [N]' 里的字节数 N："
        "N>0 说明串口链路活（问题在驱动/应用层），收不到 RX 行或 N=0 说明物理链路死"
        "（该交硬件，绝不能上报为唤醒率=0）。当 logcat 层面无法区分'真没醒'和'链路故障'时调用。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "check_type": {
                "type": "string",
                "enum": ["wakeup", "audio", "network"],
                "description": "诊断的场景类型",
            }
        },
        "required": ["check_type"],
    },
    func=serial_diagnose,
)

ALL_TOOLS = [ADB_TOOL, LOGCAT_TOOL, SERIAL_TOOL]

