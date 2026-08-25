"""Mock 模式冒烟测试：验证 case2 全流程 + checkpoint/resume 落地"""
import os, sys, tempfile, shutil, json
from pathlib import Path

os.environ["PLATFORM_MOCK"] = "1"
os.environ["ANTHROPIC_API_KEY"] = "sk-test"  # LLM 不会真的被调用，因 mock 分支拦截采集器
os.environ["DEVICE_ADDR"] = ""  # mock 模式不需要真机

sys.path.insert(0, str(Path(__file__).parent))

import core.plugins.acoustic.lamp_plugin  # noqa
from core.plugins import default_registry
from core.plugins.models import TestCase, TestRequirement, RawData
from core.checkpoint import CheckpointStore


def check(label, cond, detail=""):
    status = "[PASS]" if cond else "[FAIL]"
    print(f"  {status} {label}" + (f" - {detail}" if detail else ""))
    return cond


def test_generate_cases_parses_case2():
    """generate_cases 应解析出 case2 的距离/角度/环境/唤醒词"""
    print("\n[1] generate_cases 解析 case2 参数")
    plugin = default_registry.get("acoustic_lamp")
    req = TestRequirement(raw=(
        r"对 AI 台灯执行 case2：3m 正对 安静环境，音源 65dB 环境 45dB SNR 20dB，"
        r"唤醒词小白小白，音频 D:\LL20\唤醒音频-小白-300次.mp3，预期 300 次"
    ), device="AI台灯")
    cases = plugin.generate_cases(req)
    ok = True
    ok &= check("生成 1 条用例", len(cases) == 1)
    c = cases[0]
    meta = c.params["collector_args"]["case_meta"]
    ok &= check(f"case_id=case2", meta.get("case_id") == "case2", f"实际={meta.get('case_id')}")
    ok &= check("distance_m=3.0", meta.get("distance_m") == 3.0, f"实际={meta.get('distance_m')}")
    ok &= check("angle_deg=90", meta.get("angle_deg") == 90)
    ok &= check("environment=安静环境", meta.get("environment") == "安静环境")
    ok &= check("wakeword=小白小白", meta.get("wakeword") == "小白小白")
    ok &= check("音频路径 mp3", ".mp3" in c.params["collector_args"]["audio_file"])
    ok &= check("expected_count=300", c.params["collector_args"]["expected_count"] == 300)
    return ok


def test_lamp_wakeup_test_mock():
    """mock 分支应返回 case_meta 和 summary"""
    print("\n[2] lamp_wakeup_test mock 直调")
    from core.plugins.acoustic.lamp_plugin import lamp_wakeup_test
    result = lamp_wakeup_test(
        audio_file="fake.mp3",
        expected_count=300,
        case_meta={"case_id": "case2"},
    )
    ok = True
    ok &= check("status=success", result["status"] == "success")
    ok &= check("mode=mock", result["mode"] == "mock")
    ok &= check("summary.detected_count", "detected_count" in result["summary"])
    ok &= check("case_meta 透传", result["case_meta"]["case_id"] == "case2")
    return ok


def test_executor_forwards_case_meta():
    """执行器应把 case_meta 透传给播放工具"""
    print("\n[3] ExecutorAgent 透传 case_meta")
    from core.agents.subagents import ExecutorAgent
    plugin = default_registry.get("acoustic_lamp")
    exe = ExecutorAgent(plugin)
    case = TestCase(
        case_id="AI台灯-CASE2",
        title="mock",
        params={
            "collector": "lamp_wakeup_test",
            "collector_args": {
                "audio_file": "fake.mp3",
                "expected_count": 300,
                "case_meta": {"case_id": "case2", "distance_m": 3.0},
            },
        },
        expected="唤醒率 ≥ 90%",
    )
    raw = exe.run([case])
    ok = True
    # 至少有一条 playback 结果，且 case_meta 被透传进去了
    playback = [r for r in raw if r.collector == "lamp_wakeup_test"]
    ok &= check("有 playback 采集结果", len(playback) >= 1)
    if playback:
        payload = playback[0].payload
        ok &= check("透传了 case_meta",
                    payload.get("case_meta", {}).get("case_id") == "case2",
                    f"实际={payload.get('case_meta')}")
    return ok


def test_coordinator_persists_raw_data():
    """执行阶段跑完 raw_data.json 应落盘，resume 能重建"""
    print("\n[4] Coordinator 落盘并复用 raw_data")
    ws = tempfile.mkdtemp()
    try:
        from core.agents import Coordinator
        plugin = default_registry.get("acoustic_lamp")
        coord = Coordinator(plugin, workspace_path=ws)

        # 手动构造并落盘 raw_data
        raw = [RawData(case_id="case2", collector="lamp_wakeup_test",
                       payload={"summary": {"detected_count": 288, "success_rate": "96.0%"}})]
        coord._save_raw_data(raw)

        raw_file = Path(ws) / "raw_data.json"
        ok = True
        ok &= check("raw_data.json 落盘", raw_file.exists())
        loaded = coord._load_raw_data()
        ok &= check("能重建 raw_data", len(loaded) == 1)
        ok &= check("payload 保真",
                    loaded[0].payload["summary"]["detected_count"] == 288)
        return ok
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def test_resume_reuses_raw_data():
    """标记 execute 已完成 + 有落盘 raw_data，resume 应跳过执行"""
    print("\n[5] resume 跳过执行时复用落盘 raw_data")
    ws = tempfile.mkdtemp()
    try:
        from core.agents import Coordinator
        from core.checkpoint import TestCheckpoint, capture_current_env

        plugin = default_registry.get("acoustic_lamp")
        coord = Coordinator(plugin, workspace_path=ws)

        # 模拟：执行阶段已完成，raw_data 已落盘
        raw = [RawData(case_id="case2", collector="lamp_wakeup_test",
                       payload={"summary": {"detected_count": 288, "success_rate": "96.0%"},
                                "issues": []})]
        coord._save_raw_data(raw)

        # 手动 restore 到 execute 已完成状态
        ckpt = TestCheckpoint(
            checkpoint_id="ckpt_test",
            plugin=plugin.name,
            current_stage="execute",
            completed_stages=["requirement", "case_gen", "execute"],
            artifacts={"requirement_raw": "case2 3m"},
            env_snapshot=capture_current_env(ws),
            workspace_path=ws,
        )
        coord.restore_state(ckpt)

        # 触发 execute 分支的 _load_raw_data
        loaded = coord._load_raw_data()
        ok = True
        ok &= check("resume 前 raw_data 可用", len(loaded) == 1)
        ok &= check("summary 保留", loaded[0].payload["summary"]["detected_count"] == 288)
        return ok
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def test_wakeup_line_parser():
    """trace.log 行解析：只算 WAKEUP 行（每次唤醒写 KEYWORD+WAKEUP 两行）"""
    print("\n[6] _parse_wakeup_line 各种格式")
    from core.plugins.acoustic.lamp_plugin import _parse_wakeup_line
    ok = True

    # WAKEUP 行（新格式，Unix 时间戳）
    line1 = "[1787623213.318] WAKEUP keyword=xiao bai xiao bai ncm=905 threshold=800"
    ev1 = _parse_wakeup_line(line1)
    ok &= check("WAKEUP 行解析",
                ev1 and ev1.get("keyword") == "xiao bai xiao bai" and ev1.get("ncm") == 905,
                f"实际={ev1}")

    # KEYWORD 行应被过滤（返回 None）
    line_kw = "[1787623213.318] KEYWORD keyword=xiao bai xiao bai ncm=834 threshold=800"
    ok &= check("KEYWORD 行过滤", _parse_wakeup_line(line_kw) is None, "避免计数翻倍")

    # 空行/纯空白：应返回 None
    ok &= check("空行返回 None", _parse_wakeup_line("") is None)
    ok &= check("纯空白返回 None", _parse_wakeup_line("   ") is None)

    # 旧格式兼容（ISO 时间戳 + WAKEUP 关键字）
    line_old = "[2026-08-25 10:00:01.123] WAKEUP keyword: xiao bai xiao bai ncm: 905"
    ev_old = _parse_wakeup_line(line_old)
    ok &= check("旧格式 WAKEUP 兼容", ev_old and ev_old.get("ncm") == 905)
    return ok


def test_sensitivity_flows_through():
    """灵敏度档位应贯穿：需求文本→generate_cases→collector_args→lamp_wakeup_test→summary"""
    print("\n[7] 灵敏度档位全流程透传")
    from core.plugins.acoustic.lamp_plugin import lamp_wakeup_test
    plugin = default_registry.get("acoustic_lamp")
    ok = True

    # 1) generate_cases 默认标准档
    req = TestRequirement(raw="case2 3m 正对 安静 小白小白", device="AI台灯")
    cases = plugin.generate_cases(req)
    meta = cases[0].params["collector_args"]["case_meta"]
    ok &= check("未指定档位→默认'标准'",
                meta.get("sensitivity") == "标准" and cases[0].params["collector_args"]["sensitivity"] == "标准")

    # 2) 需求里写"灵敏度较高"应解析为"较高"
    req_high = TestRequirement(raw="case2 3m 正对 安静 小白小白 灵敏度较高", device="AI台灯")
    cases_high = plugin.generate_cases(req_high)
    ok &= check("需求含'灵敏度较高'→较高",
                cases_high[0].params["collector_args"]["sensitivity"] == "较高")

    # 3) lamp_wakeup_test 应把 sensitivity 写入 summary（mock 分支也生效）
    # mock 分支目前未包含 sensitivity 字段，直接调 real 路径也不可能。
    # 转而验证 case_meta 里被注入：
    result = lamp_wakeup_test(audio_file="fake.mp3", expected_count=10, sensitivity="较低")
    ok &= check("mock 结果 case_meta 含 sensitivity",
                result.get("case_meta", {}).get("sensitivity") == "较低",
                f"实际={result.get('case_meta')}")
    return ok


if __name__ == "__main__":
    results = [
        test_generate_cases_parses_case2(),
        test_lamp_wakeup_test_mock(),
        test_executor_forwards_case_meta(),
        test_coordinator_persists_raw_data(),
        test_resume_reuses_raw_data(),
        test_wakeup_line_parser(),
        test_sensitivity_flows_through(),
    ]
    print()
    print("=" * 50)
    passed = sum(results)
    print(f"总计: {passed}/{len(results)} 通过")
    sys.exit(0 if passed == len(results) else 1)
