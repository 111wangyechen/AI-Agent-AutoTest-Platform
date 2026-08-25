"""
台灯插件 · iperf3 解析器单测

用 8/19 真实 LL10 数据校验解析正确性。这些数字是面试要脱口而出的：
平均 26.5Mbps、总重传 2600、单秒峰值重传 88、拥塞崩溃导致 3 秒断流。
"""

from core.plugins.lamp.tools import _parse_iperf3, _MOCK_IPERF3, iperf3_throughput, call_interop


def test_parse_real_ll10_summary():
    r = _parse_iperf3(_MOCK_IPERF3)
    assert r["status"] == "success"
    res = r["result"]
    assert res["overall_avg_mbps"] == 26.5      # 汇总行平均带宽
    assert res["total_retr"] == 2600            # 汇总行总重传
    assert res["max_retr_per_sec"] == 88        # 单秒重传峰值


def test_parse_detects_congestion_collapse():
    res = _parse_iperf3(_MOCK_IPERF3)["result"]
    assert res["min_mbps"] == 0.0               # 出现吞吐归零
    assert res["stall_seconds"] == 3            # 382/384/385s 三秒断流
    assert "断流" in res["verdict"] or "崩溃" in res["verdict"]


def test_bandwidth_unit_conversion():
    # 1.05 Mbits/sec 那一行应被算成 1.05，不是 1050
    res = _parse_iperf3(_MOCK_IPERF3)["result"]
    assert res["max_mbps"] < 100                 # 都在合理量级
    assert 0 < res["avg_mbps"] < 100


def test_iperf3_tool_mock_mode():
    r = iperf3_throughput(direction="lamp_to_pc")
    assert r["status"] == "success"
    assert "note" in r and "LL10" in r["note"]


def test_parse_empty_returns_error():
    r = _parse_iperf3("no samples here")
    assert r["status"] == "error"


def test_call_csv_parses_soak_run():
    from core.plugins.lamp.tools import _parse_call_csv, _MOCK_CALL_CSV
    res = _parse_call_csv(_MOCK_CALL_CSV)["result"]
    assert res["rounds"] == 5
    assert res["peak_thread"] == 91                 # 接通态实测线程数
    assert res["reboot_rounds"] == [5]              # 第5通命中自发重启
    assert res["dropped_rounds"] == [4]             # 第4通掉话
    assert "自发重启" in res["verdict"]


def test_call_interop_mock_hits_reboot_defect():
    r = call_interop(scenario="soak")
    assert r["status"] == "success"
    assert r["result"]["reboot_rounds"] == [5]
    assert "autocall" in r["note"]


def test_call_csv_empty_returns_error():
    from core.plugins.lamp.tools import _parse_call_csv
    r = _parse_call_csv("round,start,reboot\n")   # 只有表头
    assert r["status"] == "error"


def test_call_csv_all_normal_verdict():
    from core.plugins.lamp.tools import _parse_call_csv
    csv = (
        "round,start,dialed,ringing,answered,thr_peak,talk_sec,hangup,reboot,hang,note\n"
        "1,t,1,1,1,91,60.0,1,0,0,\n2,t,1,1,1,91,60.0,1,0,0,\n"
    )
    res = _parse_call_csv(csv)["result"]
    assert res["reboot_rounds"] == [] and res["answer_rate"] == 1.0
    assert "全部正常" in res["verdict"]
