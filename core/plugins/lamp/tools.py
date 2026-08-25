"""
台灯插件 · 采集/诊断工具

- iperf3_throughput: 解析 iperf3 双向吞吐日志，算平均/最低带宽、最大重传、Cwnd 波动。
  真解析器：mock 模式用内联的 8/19 真实 LL10 数据（含拥塞崩溃段）；
  真机模式跑 adb shell iperf3 或读取日志文件。
- call_interop:      解析 autocall 长跑工具的 rounds CSV，聚合通话稳定性 / 自发重启缺陷。

数据来源：
- iperf3：docs/0819对比测试/LL10/电脑收台灯发/（600s 双向压测真日志）
- 通话：autocall_tool_20260821/（cmcc_vchat 通话重启缺陷复现工具，产出 rounds_<run_id>.csv）
"""

from __future__ import annotations

import re
from typing import Any

from core.config import config
from core.tools import Tool


# 内联一段真实 LL10 台灯→电脑日志（600s 里的代表性片段，含 88 次重传峰值 + 382~385s 拥塞崩溃）
_MOCK_IPERF3 = """\
Connecting to host 192.168.211.2, port 5201
[  4] local 192.168.211.5 port 48532 connected to 192.168.211.2 port 5201
[ ID] Interval           Transfer     Bandwidth       Retr  Cwnd
[  4]   0.00-1.00   sec  3.19 MBytes  26.7 Mbits/sec    1    180 KBytes
[  4]   1.00-2.00   sec  3.25 MBytes  27.3 Mbits/sec   88    135 KBytes
[  4]   2.00-3.00   sec  4.12 MBytes  34.6 Mbits/sec    0    164 KBytes
[  4]   3.00-4.00   sec  3.62 MBytes  30.4 Mbits/sec    0    181 KBytes
[  4]   4.00-5.00   sec  3.25 MBytes  27.3 Mbits/sec    1    141 KBytes
[  4] 381.00-382.00 sec  1.25 MBytes  10.5 Mbits/sec    2   4.28 KBytes
[  4] 382.00-383.00 sec  0.00 Bytes  0.00 bits/sec    9   7.13 KBytes
[  4] 383.00-384.00 sec   128 KBytes  1.05 Mbits/sec   29   1.43 KBytes
[  4] 384.00-385.00 sec  0.00 Bytes  0.00 bits/sec   11   1.43 KBytes
[  4] 385.00-386.00 sec  0.00 Bytes  0.00 bits/sec    9   2.85 KBytes
[  4]   0.00-600.00 sec  1.85 GBytes  26.5 Mbits/sec  2600             sender
[  4]   0.00-600.00 sec  1.85 GBytes  26.5 Mbits/sec                  receiver
"""


def _to_mbps(value: float, unit: str) -> float:
    """把 iperf3 的带宽换算成 Mbits/sec。"""
    unit = unit.lower()
    if unit.startswith("k"):
        return value / 1000.0
    if unit.startswith("g"):
        return value * 1000.0
    return value  # M


def _parse_iperf3(raw: str) -> dict[str, Any]:
    """解析 iperf3 文本输出，抽取逐秒采样与汇总。核心指标：带宽/重传/拥塞窗口。"""
    interval_re = re.compile(
        r"sec\s+[\d.]+\s+\w+\s+([\d.]+)\s+([KMG]?)bits/sec\s+(\d+)\s+([\d.]+)\s+([KMG]?)Bytes"
    )
    samples = []
    for line in raw.splitlines():
        if "sender" in line or "receiver" in line:
            continue
        m = interval_re.search(line)
        if m:
            bw = _to_mbps(float(m.group(1)), m.group(2))
            samples.append({"mbps": bw, "retr": int(m.group(3))})
    if not samples:
        return {"status": "error", "result": "未解析到 iperf3 采样行"}

    bws = [s["mbps"] for s in samples]
    retrs = [s["retr"] for s in samples]
    summary_bw = re.search(r"sec\s+[\d.]+\s+GBytes\s+([\d.]+)\s+Mbits/sec\s+(\d+)\s+sender", raw)
    zero_windows = sum(1 for b in bws if b == 0.0)
    return {
        "status": "success",
        "result": {
            "avg_mbps": round(sum(bws) / len(bws), 2),
            "min_mbps": round(min(bws), 3),
            "max_mbps": round(max(bws), 2),
            "max_retr_per_sec": max(retrs),
            "total_retr": int(summary_bw.group(2)) if summary_bw else sum(retrs),
            "overall_avg_mbps": float(summary_bw.group(1)) if summary_bw else None,
            "stall_seconds": zero_windows,
            "sample_count": len(samples),
            "verdict": _throughput_verdict(bws, retrs, zero_windows),
        },
    }


def _throughput_verdict(bws: list[float], retrs: list[int], stalls: int) -> str:
    """基于带宽/重传/断流给一句人类可读的判据。"""
    if stalls > 0:
        return f"检测到 {stalls} 秒吞吐归零（拥塞崩溃/断流），链路稳定性差"
    if max(retrs) > 50:
        return f"重传峰值 {max(retrs)}/s 偏高，存在信道竞争或干扰"
    return "吞吐平稳，无明显重传或断流"


# ---------- 工具 1：iperf3 吞吐 ----------

def iperf3_throughput(direction: str = "lamp_to_pc", log_path: str = "") -> dict[str, Any]:
    """
    采集/解析 iperf3 双向吞吐。mock 或无设备时用内联真实 LL10 数据；
    给定 log_path 时读取该日志文件；真机模式跑 adb shell iperf3。
    """
    if log_path:
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                return _parse_iperf3(f.read())
        except OSError as e:
            return {"status": "error", "result": f"读取日志失败: {e}"}

    if config.mock or not config.has_device:
        out = _parse_iperf3(_MOCK_IPERF3)
        out["note"] = "mock：内联 8/19 真实 LL10 台灯→电脑 600s 数据"
        return out

    # 真机：设备上跑 iperf3 客户端打到电脑上的 server
    from core.plugins.acoustic.tools import _run_adb  # 复用 adb 执行器
    rc, stdout, err = _run_adb(
        ["shell", "iperf3", "-c", config.iperf3_server, "-i", "1", "-t", "30"], timeout=60
    )
    if rc != 0:
        return {"status": "error", "returncode": rc, "result": (err or stdout).strip()}
    return _parse_iperf3(stdout)


# ---------- 工具 2：台灯↔手机通话互通（接入 autocall 长跑工具） ----------
#
# 真实工具：autocall_tool_20260821/（cmcc_vchat 引擎，复现"通话中自发重启"缺陷）。
# 它长跑 拨出→接听→保持60s→挂断 循环，每通写一行 rounds_<run_id>.csv：
#   round,start,dialed,ringing,answered,thr_peak,talk_sec,hangup,reboot,hang,note
# 平台把这份 CSV 当作可分析 artifact——就像 iperf3 那样解析、聚合、给判据。

# 内联一段代表性 rounds CSV：前若干通正常，第 5 通命中自发重启（真缺陷）。
# 线程数三态实测 空闲53/响铃54/接通91，故正常通 thr_peak≈91。
_MOCK_CALL_CSV = """\
round,start,dialed,ringing,answered,thr_peak,talk_sec,hangup,reboot,hang,note
1,2026-08-21 09:00:00,1,1,1,91,60.0,1,0,0,
2,2026-08-21 09:01:15,1,1,1,91,60.0,1,0,0,
3,2026-08-21 09:02:30,1,1,1,90,60.0,1,0,0,
4,2026-08-21 09:03:45,1,1,1,91,42.3,1,0,0,通话意外中断（42.3s 时掉话）
5,2026-08-21 09:05:00,1,1,1,91,37.8,0,1,0,通话中检测到重启
"""


def _parse_call_csv(raw: str) -> dict[str, Any]:
    """解析 autocall rounds CSV，聚合出接通率/挂断成功率/重启命中/掉话等判据。"""
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows:
        return {"status": "error", "result": "未解析到通话轮次"}

    total = len(rows)
    answered = sum(1 for r in rows if r.get("answered") == "1")
    hangup_ok = sum(1 for r in rows if r.get("hangup") == "1")
    reboots = [int(r["round"]) for r in rows if r.get("reboot") == "1"]
    hangs = [int(r["round"]) for r in rows if r.get("hang") == "1"]
    dropped = [int(r["round"]) for r in rows if "掉话" in (r.get("note") or "")]
    peak_thr = max((int(r["thr_peak"]) for r in rows if r.get("thr_peak")), default=0)

    return {
        "status": "success",
        "result": {
            "rounds": total,
            "answer_rate": round(answered / total, 3),
            "hangup_success_rate": round(hangup_ok / total, 3),
            "reboot_rounds": reboots,
            "hang_rounds": hangs,
            "dropped_rounds": dropped,
            "peak_thread": peak_thr,   # 接通态实测≈91
            "verdict": _call_verdict(reboots, hangs, dropped, total),
        },
    }


def _call_verdict(reboots, hangs, dropped, total) -> str:
    """基于长跑结果给一句人类可读判据。自发重启是最高优先级的真缺陷。"""
    if reboots:
        return f"命中自发重启缺陷：第 {reboots} 通通话中设备重启（共 {total} 通）"
    if dropped:
        return f"第 {dropped} 通通话意外掉话，链路/媒体不稳"
    if hangs:
        return f"第 {hangs} 通卡死/忙线，触发恢复"
    return f"{total} 通全部正常接通并挂断，无重启"


def call_interop(scenario: str = "soak", csv_path: str = "") -> dict[str, Any]:
    """
    台灯↔手机通话互通自动化（接入 autocall 长跑工具）。

    给定 csv_path 时解析该 rounds CSV；mock/无设备时用内联真实缺陷样本
    （第 5 通命中通话中自发重启）；真机模式提示用 autocall.py 现场长跑产出 CSV。
    """
    if csv_path:
        try:
            with open(csv_path, encoding="utf-8-sig", errors="replace") as f:
                return _parse_call_csv(f.read())
        except OSError as e:
            return {"status": "error", "result": f"读取通话 CSV 失败: {e}"}

    if config.mock or not config.has_device:
        out = _parse_call_csv(_MOCK_CALL_CSV)
        out["note"] = "mock：autocall 长跑样本（cmcc_vchat 通话重启缺陷复现，第5通命中）"
        return out

    return {
        "status": "error",
        "result": (
            "真机长跑请运行 autocall_tool_20260821/autocall.py 产出 rounds_<run_id>.csv，"
            "再用 csv_path 传入解析"
        ),
    }


# ---------- 包装成通用内核的 Tool ----------

IPERF3_TOOL = Tool(
    name="iperf3_throughput",
    description=(
        "采集并解析 iperf3 双向吞吐日志，产出平均/最低/最高带宽、每秒最大重传数、"
        "吞吐归零秒数与稳定性判据。定位 AI 台灯 WiFi 性能：带宽骤降看干扰/信道，"
        "重传高看拥塞，Cwnd 塌缩看链路质量。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "direction": {"type": "string", "description": "lamp_to_pc / pc_to_lamp"},
            "log_path": {"type": "string", "description": "可选：直接解析的 iperf3 日志文件路径"},
        },
        "required": [],
    },
    func=iperf3_throughput,
)

CALL_TOOL = Tool(
    name="call_interop",
    description=(
        "解析 autocall 通话长跑工具产出的 rounds CSV（拨出→接听→保持→挂断循环），"
        "聚合接通率/挂断成功率/掉话轮次/卡死轮次/自发重启轮次。"
        "定位 AI 台灯 cmcc_vchat 通话链路稳定性——尤其'通话中自发重启'缺陷。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "scenario": {"type": "string", "description": "soak（长跑复现），预留"},
            "csv_path": {"type": "string", "description": "可选：autocall rounds_<run_id>.csv 路径"},
        },
        "required": [],
    },
    func=call_interop,
)

ALL_TOOLS = [IPERF3_TOOL, CALL_TOOL]
