"""
环境快照采集 · capture_current_env()

这是「测试前 Agent 驱动抓取设备状态」的确定性底座：
adb 读设备版本/IP/uptime/硬件基线，算 workspace merkle hash，测网络延迟。
PreflightAgent 会把这些采集能力包装成 Tool，让 LLM 自主决定抓哪些、怎么对比。

mock / 无设备时回落到可预测的样本值，方便在家跑单测。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from core.config import config
from .models import EnvironmentSnapshot


# ---------- adb 底层 ----------

def _run_adb(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
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
        return 127, "", "adb 未安装或不在 PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"adb 超时(>{timeout}s)"


# ---------- 各锚点采集（可单独作为 Tool 暴露给 PreflightAgent）----------

def read_device_version() -> str:
    """设备固件版本：优先 build.prop，回落 getprop。"""
    if config.mock or not config.has_device:
        return "1.0.3.a12"
    # AI 台灯是 Linux，版本在 build.prop 的 ro.* 或日志 fw ver
    rc, out, _ = _run_adb(["shell", "cat /etc/version 2>/dev/null || cat /build.prop 2>/dev/null | grep -i ver"])
    if rc == 0 and out.strip():
        return out.strip().splitlines()[0][:64]
    rc, out, _ = _run_adb(["shell", "getprop ro.build.version.release"])
    return out.strip() if rc == 0 else "unknown"


def read_device_uptime() -> float:
    """/proc/uptime 首字段（秒）。用于检测设备是否重启（uptime 变小=重启过）。"""
    if config.mock or not config.has_device:
        return 3600.0
    rc, out, _ = _run_adb(["shell", "cat /proc/uptime"])
    if rc == 0 and out.strip():
        try:
            return float(out.split()[0])
        except (ValueError, IndexError):
            return 0.0
    return 0.0


def read_device_serial() -> str:
    if config.mock or not config.has_device:
        return "MOCK-SERIAL-0001"
    rc, out, _ = _run_adb(["shell", "getprop ro.serialno"])
    if rc == 0 and out.strip():
        return out.strip()
    # 台灯序列号在日志 gSCMK.sn，回落用 device_addr
    return config.device_addr or "unknown"


def read_device_baseline() -> dict:
    """硬件基线：内存空闲、负载、温度（能读到多少读多少）。"""
    if config.mock or not config.has_device:
        return {"mem_free_kb": 512000, "loadavg": 0.5, "temp": 35}
    baseline: dict = {}
    rc, out, _ = _run_adb(["shell", "cat /proc/loadavg"])
    if rc == 0 and out.strip():
        try:
            baseline["loadavg"] = float(out.split()[0])
        except (ValueError, IndexError):
            pass
    rc, out, _ = _run_adb(["shell", "cat /proc/meminfo"])
    if rc == 0:
        m = re.search(r"MemFree:\s+(\d+)", out)
        if m:
            baseline["mem_free_kb"] = int(m.group(1))
    return baseline


def measure_network_latency() -> int:
    """ping 设备一次，返回毫秒延迟；不可达返回 -1。"""
    if config.mock or not config.device_addr:
        return 5  # mock 固定值
    ip = config.device_addr.split(":")[0]
    # Windows ping：-n 1 一次，-w 2000 超时 2s
    try:
        proc = subprocess.run(
            ["ping", "-n", "1", "-w", "2000", ip],
            capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
        )
        m = re.search(r"[=<](\d+)ms", proc.stdout)
        return int(m.group(1)) if m else -1
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return -1


def compute_workspace_hash(workspace_path: str) -> str:
    """
    workspace 目录的 merkle hash：对所有文件的 (相对路径, 大小, mtime) 排序后哈希。

    不读文件内容（大文件太慢），用路径+大小+mtime 足以检出增删改。
    跳过 .checkpoints 自身，避免打点时自我扰动。
    """
    ws = Path(workspace_path)
    if not ws.exists():
        return ""
    h = hashlib.sha256()
    entries = []
    for p in sorted(ws.rglob("*")):
        if ".checkpoints" in p.parts:
            continue
        if p.is_file():
            try:
                st = p.stat()
                rel = p.relative_to(ws).as_posix()
                entries.append(f"{rel}|{st.st_size}|{int(st.st_mtime)}")
            except OSError:
                continue
    for e in entries:
        h.update(e.encode("utf-8"))
    return h.hexdigest()[:16]


def sha256_file(path: str) -> str:
    """单个文件内容的 sha256（用于音频文件损坏检测）。"""
    p = Path(path)
    if not p.exists():
        return ""
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()[:16]


def compute_config_hash() -> str:
    """当前测试配置的 hash：把影响测试的 config 字段拼起来哈希。"""
    parts = [
        config.model,
        config.device_addr,
        str(config.logcat_lines),
        config.iperf3_server,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def capture_current_env(workspace_path: str = "") -> EnvironmentSnapshot:
    """
    抓取当前完整环境快照。checkpoint 打点时存它，resume 时再抓一份对比。

    这是 PreflightAgent 「测试前抓取设备状态」的确定性实现；Agent 也可以
    只调用上面的单项函数做更细粒度的自主研判。
    """
    ip = config.device_addr.split(":")[0] if config.device_addr else ""
    return EnvironmentSnapshot(
        device_rom_version=read_device_version(),
        device_serial=read_device_serial(),
        device_ip=ip,
        device_uptime_s=read_device_uptime(),
        device_baseline=read_device_baseline(),
        workspace_hash=compute_workspace_hash(workspace_path) if workspace_path else "",
        collected_files=[],
        checkpoint_integrity="",
        network_latency_ms=measure_network_latency(),
        cloud_api_latency_ms=-1,
        test_config_hash=compute_config_hash(),
        captured_at=datetime.now().isoformat(timespec="seconds"),
    )
