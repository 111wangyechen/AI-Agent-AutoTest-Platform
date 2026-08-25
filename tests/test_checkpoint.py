"""
Checkpoint / Resume + 漂移检测单测

对应面试稿承诺的确定性测试：
- checkpoint 存取、resume 跳过已完成阶段
- 漂移检测 10 场景、三级严重程度
- checkpoint 损坏降级到上一个快照
- 故障注入：版本变更/设备重启/配置篡改
"""

import copy
import tempfile
import time
from pathlib import Path

import pytest

from core.checkpoint import (
    CheckpointStore,
    TestCheckpoint,
    capture_current_env,
    detect_drift,
)
from core.checkpoint.models import (
    DriftSeverity,
    DriftType,
    EnvironmentSnapshot,
)


@pytest.fixture
def workspace():
    return tempfile.mkdtemp()


def _make_ckpt(ws, cid, completed, snap=None):
    return TestCheckpoint(
        checkpoint_id=cid,
        plugin="acoustic_lamp",
        current_stage=completed[-1] if completed else "requirement",
        completed_stages=list(completed),
        env_snapshot=snap or capture_current_env(ws),
        workspace_path=ws,
    )


# ==================== Checkpoint 存取 ====================

def test_store_roundtrip(workspace):
    """存进去能原样读出来。"""
    store = CheckpointStore(workspace)
    ck = _make_ckpt(workspace, "ckpt_20260824_120000_000001", ["requirement"])
    store.save(ck)
    loaded = store.load_latest()
    assert loaded is not None
    assert loaded.checkpoint_id == ck.checkpoint_id
    assert loaded.plugin == "acoustic_lamp"


def test_resume_skips_done_stages(workspace):
    """resume 时已完成阶段应被识别为 done（面试稿承诺）。"""
    store = CheckpointStore(workspace)
    ck = _make_ckpt(workspace, "ckpt_20260824_120000_000001", ["requirement", "case_gen"])
    store.save(ck)
    state = store.load_latest()
    assert state.done("requirement") is True
    assert state.done("case_gen") is True
    assert state.done("execute") is False


def test_store_keeps_only_recent_three(workspace):
    """只保留最近 3 个 checkpoint，旧的清理。"""
    store = CheckpointStore(workspace)
    for i in range(5):
        store.save(_make_ckpt(workspace, f"ckpt_20260824_12000{i}_00000{i}", ["requirement"]))
        time.sleep(0.005)
    assert len(store.list_checkpoints()) == 3


def test_corrupted_latest_falls_back(workspace):
    """最新 checkpoint 损坏时，降级读上一个（面试稿承诺的降级路径）。"""
    store = CheckpointStore(workspace)
    store.save(_make_ckpt(workspace, "ckpt_20260824_120000_000001", ["requirement"]))
    time.sleep(0.01)
    store.save(_make_ckpt(workspace, "ckpt_20260824_120001_000002", ["requirement", "case_gen"]))
    # 写坏最新文件
    (Path(workspace) / ".checkpoints" / "ckpt_20260824_120001_000002.json").write_text(
        "{ broken", encoding="utf-8"
    )
    loaded = store.load_latest()
    assert loaded is not None
    assert loaded.checkpoint_id == "ckpt_20260824_120000_000001"


def test_no_checkpoint_returns_none(workspace):
    assert CheckpointStore(workspace).load_latest() is None


# ==================== 漂移检测 ====================

def _base_snapshot():
    """一份稳定的基线快照，各测试拷贝后改单个字段做故障注入。"""
    return EnvironmentSnapshot(
        device_rom_version="1.0.3.a12",
        device_serial="SN-0001",
        device_ip="10.7.187.152",
        device_uptime_s=3600.0,
        device_baseline={"mem_free_kb": 512000, "loadavg": 0.5},
        workspace_hash="abc123",
        collected_files=[],
        checkpoint_integrity="ck-hash-1",
        network_latency_ms=5,
        cloud_api_latency_ms=-1,
        test_config_hash="cfg-hash-1",
        captured_at="2026-08-24T12:00:00",
    )


def test_no_drift_when_identical():
    old = _base_snapshot()
    result = detect_drift(old, copy.deepcopy(old))
    assert result.detected is False
    assert result.severity == DriftSeverity.NONE


def test_drift_detected_on_version_change():
    """故障注入：设备版本变了，必须检出漂移，判 MEDIUM（面试稿承诺）。"""
    old = _base_snapshot()
    new = copy.deepcopy(old)
    new.device_rom_version = "1.3.0"
    result = detect_drift(old, new)
    assert result.detected is True
    assert DriftType.DEVICE_VERSION_CHANGED in {i.drift_type for i in result.items}
    assert result.severity == DriftSeverity.MEDIUM


def test_drift_detected_on_device_reboot():
    """uptime 回退 => 设备重启过。"""
    old = _base_snapshot()
    new = copy.deepcopy(old)
    new.device_uptime_s = 30.0                    # 远小于 3600 + 间隔
    new.captured_at = "2026-08-24T12:05:00"       # 只过了 5 分钟，uptime 却回到 30s
    result = detect_drift(old, new)
    assert DriftType.DEVICE_REBOOT in {i.drift_type for i in result.items}
    assert result.severity == DriftSeverity.MEDIUM


def test_drift_detected_on_ip_change():
    old = _base_snapshot()
    new = copy.deepcopy(old)
    new.device_ip = "10.7.187.200"
    result = detect_drift(old, new)
    assert DriftType.DEVICE_IP_CHANGED in {i.drift_type for i in result.items}


def test_drift_detected_on_config_tamper_is_high():
    """配置被改 => HIGH => 转人工。"""
    old = _base_snapshot()
    new = copy.deepcopy(old)
    new.test_config_hash = "cfg-hash-TAMPERED"
    result = detect_drift(old, new)
    assert DriftType.TEST_CONFIG_MODIFIED in {i.drift_type for i in result.items}
    assert result.severity == DriftSeverity.HIGH


def test_drift_detected_on_checkpoint_tamper_is_high():
    old = _base_snapshot()
    new = copy.deepcopy(old)
    new.checkpoint_integrity = "ck-hash-TAMPERED"
    result = detect_drift(old, new)
    assert DriftType.CHECKPOINT_TAMPERED in {i.drift_type for i in result.items}
    assert result.severity == DriftSeverity.HIGH


def test_drift_network_degraded_to_unreachable():
    old = _base_snapshot()
    new = copy.deepcopy(old)
    new.network_latency_ms = -1  # 变不可达
    result = detect_drift(old, new)
    assert DriftType.NETWORK_DEGRADED in {i.drift_type for i in result.items}


def test_drift_workspace_files_modified():
    old = _base_snapshot()
    new = copy.deepcopy(old)
    new.workspace_hash = "def456"
    result = detect_drift(old, new)
    assert DriftType.WORKSPACE_FILES_MODIFIED in {i.drift_type for i in result.items}


def test_drift_acoustic_env_on_long_gap():
    """中断超过 1 小时 => 声学环境需重新校准。"""
    old = _base_snapshot()
    new = copy.deepcopy(old)
    new.captured_at = "2026-08-24T14:30:00"  # 过了 2.5 小时
    result = detect_drift(old, new)
    assert DriftType.ACOUSTIC_ENV_CHANGED in {i.drift_type for i in result.items}


def test_drift_hardware_baseline_exceeded():
    """空闲内存跌破基线一半 => 硬件状态漂移。"""
    old = _base_snapshot()
    new = copy.deepcopy(old)
    new.device_baseline = {"mem_free_kb": 100000, "loadavg": 3.0}  # < 512000/2
    result = detect_drift(old, new)
    assert DriftType.DEVICE_HARDWARE_CHANGED in {i.drift_type for i in result.items}


def test_all_ten_drift_types_defined():
    """兑现面试口径：10 个设计枚举场景。"""
    assert len(list(DriftType)) == 10


# ==================== Coordinator resume 集成 ====================

def test_coordinator_writes_checkpoints_per_stage(workspace):
    """跑完整流水线时，每阶段自动打点，checkpoint 落盘。"""
    import core.plugins.acoustic.lamp_plugin  # noqa: F401
    from core.plugins import default_registry
    from core.agents import Coordinator

    coord = Coordinator(default_registry.get("acoustic_lamp"), workspace_path=workspace)
    # mock 模式下子 agent 仍会调 LLM；这里只验证打点机制不依赖 LLM——
    # 直接调用内部打点助手，确认落盘与 rotate 生效
    coord._checkpoint("requirement", ["requirement"], {"requirement_raw": "唤醒测试"})
    coord._checkpoint("case_gen", ["requirement", "case_gen"], {"requirement_raw": "唤醒测试"})

    store = CheckpointStore(workspace)
    latest = store.load_latest()
    assert latest is not None
    assert latest.done("requirement") and latest.done("case_gen")
    assert latest.plugin == "acoustic_lamp"


def test_restore_state_rebuilds_progress(workspace):
    """restore_state 把 checkpoint 的已完成阶段喂回主控，供 run 跳过。"""
    import core.plugins.acoustic.lamp_plugin  # noqa: F401
    from core.plugins import default_registry
    from core.agents import Coordinator

    ck = _make_ckpt(workspace, "ckpt_20260824_130000_000001",
                    ["requirement", "case_gen", "execute"])
    coord = Coordinator(default_registry.get("acoustic_lamp"), workspace_path=workspace)
    coord.restore_state(ck)
    assert set(coord._restored["completed_stages"]) == {"requirement", "case_gen", "execute"}


def test_resume_without_workspace_raises():
    """没配 workspace 就 resume 应明确报错，而不是静默。"""
    import core.plugins.acoustic.lamp_plugin  # noqa: F401
    from core.plugins import default_registry
    from core.agents import Coordinator

    coord = Coordinator(default_registry.get("acoustic_lamp"))  # 无 workspace
    with pytest.raises(RuntimeError):
        coord.resume(run_preflight=False)
