"""
PreflightAgent 真调 LLM 验证脚本

目的：单独验证漂移研判 Agent「真的调通了 LLM 且功能正确」。
- 不连真机：PLATFORM_MOCK=1，capture_current_env() 返回稳定样本基线
- 真调 LLM：走公司网关，PreflightAgent 内部是真 Claude 会话

方法：构造 checkpoint 的「旧快照」，让它与 mock 当前基线有意不一致，
触发三种场景，断言 LLM 能正确调工具→研判→决策。

用法：
  PLATFORM_MOCK=1 .venv/Scripts/python.exe test_preflight_llm.py
"""

import sys
import io
from pathlib import Path
from datetime import datetime, timedelta

# Windows 控制台 GBK 编码兜底：强制 stdout 用 UTF-8，避免打印勾叉符号崩溃
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from core.checkpoint import capture_current_env, PreflightAgent
from core.checkpoint.models import TestCheckpoint, EnvironmentSnapshot, DriftDecision


def _current_baseline() -> EnvironmentSnapshot:
    """mock 下的当前环境基线（PreflightAgent 内部也会抓到这一份）。"""
    snap = capture_current_env("")
    # 把时间戳设成"刚刚"，避免旧快照与当前间隔>1h误触发声学漂移
    snap.captured_at = datetime.now().isoformat(timespec="seconds")
    return snap


def _make_checkpoint(old_env: EnvironmentSnapshot, stage: str = "execute") -> TestCheckpoint:
    return TestCheckpoint(
        checkpoint_id="ckpt_preflight_test",
        test_id="LAMP-WAKEUP-01",
        plugin="acoustic_lamp",
        current_stage=stage,
        completed_stages=["requirement", "case_gen"],
        current_case_index=5,
        total_cases=15,
        artifacts={"requirement_raw": "AI台灯唤醒测试，播放300次唤醒词"},
        env_snapshot=old_env,
        workspace_path="",
    )


# ========== 三种测试场景 ==========

def scenario_no_drift(log):
    """场景1：环境一致 → 期望 consistent=true, 决策 CONTINUE。"""
    base = _current_baseline()
    old = EnvironmentSnapshot(**base.to_dict())  # 旧快照与当前完全一致
    # captured_at 也对齐当前，确保零漂移
    old.captured_at = base.captured_at
    ckpt = _make_checkpoint(old)

    log("\n" + "=" * 70)
    log("【场景1】环境一致，无漂移")
    log("=" * 70)
    log("旧快照 = 当前基线（版本/IP/uptime/hash 全一致）")

    result = PreflightAgent(ckpt).assess()
    _report(log, result, expect_consistent=True,
            expect_decisions=[DriftDecision.CONTINUE, DriftDecision.CONTINUE_FLAGGED])
    return result


def scenario_version_change(log):
    """场景2：设备版本变更(MEDIUM) → 期望重跑或至少判不一致。"""
    base = _current_baseline()
    old = EnvironmentSnapshot(**base.to_dict())
    old.device_rom_version = "0.9.1.old"  # 旧版本，当前是 1.0.3.a12
    old.captured_at = base.captured_at
    ckpt = _make_checkpoint(old)

    log("\n" + "=" * 70)
    log("【场景2】设备 ROM 版本变更（0.9.1.old → 1.0.3.a12）")
    log("=" * 70)
    log("预期：确定性引擎判 MEDIUM，Agent 应判不一致、倾向重跑当前阶段")

    result = PreflightAgent(ckpt).assess()
    _report(log, result, expect_consistent=False,
            expect_decisions=[DriftDecision.RERUN_STAGE, DriftDecision.ABORT_TO_HUMAN,
                              DriftDecision.CONTINUE_FLAGGED])
    return result


def scenario_config_tampered(log):
    """场景3：测试配置被篡改(HIGH) → 期望转人工。"""
    base = _current_baseline()
    old = EnvironmentSnapshot(**base.to_dict())
    old.test_config_hash = "TAMPERED_cfg_hash"  # 配置 hash 对不上
    old.captured_at = base.captured_at
    ckpt = _make_checkpoint(old)

    log("\n" + "=" * 70)
    log("【场景3】测试配置被篡改（config hash 不匹配）")
    log("=" * 70)
    log("预期：确定性引擎判 HIGH，Agent 应判不一致、转人工")

    result = PreflightAgent(ckpt).assess()
    _report(log, result, expect_consistent=False,
            expect_decisions=[DriftDecision.ABORT_TO_HUMAN, DriftDecision.RERUN_STAGE])
    return result


def _report(log, result, expect_consistent, expect_decisions):
    """打印单场景结果 + 判定是否符合预期。"""
    log(f"\n  LLM 轮数: {result['rounds']}")
    log(f"  consistent: {result['consistent']}")
    log(f"  decision:   {result['decision'].value}")
    log(f"  reason:     {result['reason']}")
    if result.get("affected_stages"):
        log(f"  affected:   {result['affected_stages']}")

    # 功能判定
    llm_ok = result["rounds"] >= 1 and result["reason"] != "(Agent 未给出依据)"
    consistent_ok = result["consistent"] == expect_consistent
    decision_ok = result["decision"] in expect_decisions

    log(f"\n  [判定] LLM调通: {'PASS' if llm_ok else 'FAIL'}  "
        f"一致性判断正确: {'PASS' if consistent_ok else 'FAIL'}  "
        f"决策合理: {'PASS' if decision_ok else 'FAIL'}")
    result["_pass"] = llm_ok and consistent_ok and decision_ok


def main():
    lines = []
    def log(msg=""):
        print(msg)
        lines.append(msg)

    log("=" * 70)
    log("PreflightAgent 真调 LLM 功能验证")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("模式: PLATFORM_MOCK=1（环境采集用样本，LLM 真调）")
    log("=" * 70)

    results = []
    try:
        results.append(("无漂移", scenario_no_drift(log)))
        results.append(("版本变更", scenario_version_change(log)))
        results.append(("配置篡改", scenario_config_tampered(log)))
    except Exception as e:
        import traceback
        log(f"\n[异常] 执行失败（可能是 LLM 调用失败）: {e}")
        log(traceback.format_exc())

    # 汇总
    log("\n" + "=" * 70)
    log("汇总")
    log("=" * 70)
    passed = 0
    for name, r in results:
        ok = r.get("_pass", False)
        passed += 1 if ok else 0
        log(f"  {name}: {'PASS' if ok else 'FAIL'}")
    log(f"\n  总计: {passed}/{len(results)} 通过")

    # 存档
    out = Path(__file__).parent / f"preflight_llm_result_{datetime.now():%Y%m%d_%H%M%S}.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    log(f"\n结果已存档: {out.name}")


if __name__ == "__main__":
    main()
