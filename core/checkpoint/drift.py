"""
漂移检测 · detect_drift()

确定性逐项对比：把 checkpoint 存的环境快照锚点，和 resume 时重新抓的当前环境
逐项比对，命中 10 种场景中的哪些，并给出三级严重程度。

分工说明：
- 本模块是「确定性对比」——规则清晰、可单测、输入固定输出固定。
- PreflightAgent（preflight_agent.py）在此之上做「非确定性研判」——LLM 结合
  测试上下文判断这些漂移到底要不要紧、下一步怎么办。
面试口径：10 场景是设计枚举，严重程度规则在配置里定义，不是实测 100%。
"""

from __future__ import annotations

from .models import (
    DriftDetectionResult,
    DriftItem,
    DriftSeverity,
    DriftType,
    EnvironmentSnapshot,
)

# 阈值配置（面试稿："判定级别的规则在配置里定义"）
NETWORK_JITTER_THRESHOLD_MS = 100   # 延迟波动超此值算网络漂移
UPTIME_REBOOT_TOLERANCE_S = 60      # 当前 uptime 比预期小于这个余量→判重启
ACOUSTIC_STALE_HOURS = 1.0          # 中断超过此时长→声学环境可能变化
MEM_BASELINE_DROP_RATIO = 0.5       # 空闲内存跌破基线一半→硬件状态漂移


def _elapsed_hours(old: EnvironmentSnapshot, new: EnvironmentSnapshot) -> float:
    """两次快照间隔小时数（解析失败返回 0）。"""
    from datetime import datetime
    try:
        t0 = datetime.fromisoformat(old.captured_at)
        t1 = datetime.fromisoformat(new.captured_at)
        return abs((t1 - t0).total_seconds()) / 3600.0
    except (ValueError, TypeError):
        return 0.0


def _classify_severity(items: list[DriftItem]) -> DriftSeverity:
    """
    三级严重程度（面试稿 Lv1/Lv2/Lv3）：
    - HIGH：配置/checkpoint 被篡改这类根本性变化 → 转人工
    - MEDIUM：设备重启/版本变更/音频损坏 → 丢 checkpoint 重跑当前阶段
    - LOW：workspace 轻微变化/网络抖动/声学时间启发 → 标注继续
    """
    types = {it.drift_type for it in items}
    if not types:
        return DriftSeverity.NONE

    high = {DriftType.CHECKPOINT_TAMPERED, DriftType.TEST_CONFIG_MODIFIED}
    medium = {
        DriftType.DEVICE_VERSION_CHANGED,
        DriftType.DEVICE_REBOOT,
        DriftType.AUDIO_FILE_CORRUPTED,
        DriftType.DEVICE_IP_CHANGED,
    }
    if types & high:
        return DriftSeverity.HIGH
    if types & medium:
        return DriftSeverity.MEDIUM
    return DriftSeverity.LOW


def detect_drift(
    old: EnvironmentSnapshot,
    new: EnvironmentSnapshot,
) -> DriftDetectionResult:
    """逐项对比 10 种漂移场景，返回结构化结果。"""
    items: list[DriftItem] = []

    # --- 1. 设备版本变化 ---
    if old.device_rom_version and old.device_rom_version != new.device_rom_version:
        items.append(DriftItem(
            DriftType.DEVICE_VERSION_CHANGED,
            old.device_rom_version, new.device_rom_version,
            "ROM 版本变更，旧数据分析口径可能失效",
        ))

    # --- 2. 设备重启（当前 uptime 明显小于"旧 uptime + 间隔"）---
    if old.device_uptime_s > 0 and new.device_uptime_s > 0:
        elapsed_s = _elapsed_hours(old, new) * 3600.0
        expected_min = old.device_uptime_s + elapsed_s - UPTIME_REBOOT_TOLERANCE_S
        if new.device_uptime_s < expected_min:
            items.append(DriftItem(
                DriftType.DEVICE_REBOOT,
                f"{old.device_uptime_s:.0f}s", f"{new.device_uptime_s:.0f}s",
                "uptime 回退，设备中途重启过，日志/缓存可能已清空",
            ))

    # --- 3. 设备 IP 变化 ---
    if old.device_ip and old.device_ip != new.device_ip:
        items.append(DriftItem(
            DriftType.DEVICE_IP_CHANGED,
            old.device_ip, new.device_ip,
            "设备重连后 IP 变化",
        ))

    # --- 4. 硬件状态超基线（空闲内存跌破一半）---
    old_mem = old.device_baseline.get("mem_free_kb")
    new_mem = new.device_baseline.get("mem_free_kb")
    if old_mem and new_mem and new_mem < old_mem * MEM_BASELINE_DROP_RATIO:
        items.append(DriftItem(
            DriftType.DEVICE_HARDWARE_CHANGED,
            f"{old_mem}KB", f"{new_mem}KB",
            "空闲内存大幅下降，设备状态偏离基线",
        ))

    # --- 5. workspace 文件被修改 ---
    if old.workspace_hash and old.workspace_hash != new.workspace_hash:
        items.append(DriftItem(
            DriftType.WORKSPACE_FILES_MODIFIED,
            old.workspace_hash, new.workspace_hash,
            "workspace 目录内容变化（文件增删改）",
        ))

    # --- 6. 音频文件损坏（逐个校验 checkpoint 记录的采集文件）---
    from .environment import sha256_file
    for f in old.collected_files:
        path, expected = f.get("path", ""), f.get("sha256", "")
        if path and expected:
            actual = sha256_file(path)
            if actual != expected:
                items.append(DriftItem(
                    DriftType.AUDIO_FILE_CORRUPTED,
                    expected, actual or "(缺失)",
                    f"采集文件校验和不匹配: {path}",
                ))
                break

    # --- 7. checkpoint 文件被篡改 ---
    if old.checkpoint_integrity and new.checkpoint_integrity \
            and old.checkpoint_integrity != new.checkpoint_integrity:
        items.append(DriftItem(
            DriftType.CHECKPOINT_TAMPERED,
            old.checkpoint_integrity, new.checkpoint_integrity,
            "checkpoint 自身校验和变化",
        ))

    # --- 8. 网络状态变化 ---
    if old.network_latency_ms >= 0 and new.network_latency_ms >= 0:
        if abs(new.network_latency_ms - old.network_latency_ms) > NETWORK_JITTER_THRESHOLD_MS:
            items.append(DriftItem(
                DriftType.NETWORK_DEGRADED,
                f"{old.network_latency_ms}ms", f"{new.network_latency_ms}ms",
                "网络延迟大幅变化，云端超时口径可能改变",
            ))
    elif old.network_latency_ms >= 0 and new.network_latency_ms < 0:
        items.append(DriftItem(
            DriftType.NETWORK_DEGRADED,
            f"{old.network_latency_ms}ms", "不可达",
            "设备网络从通畅变为不可达",
        ))

    # --- 9. 声学环境变化（时间启发：中断过久，实验室底噪可能变了）---
    if _elapsed_hours(old, new) > ACOUSTIC_STALE_HOURS:
        items.append(DriftItem(
            DriftType.ACOUSTIC_ENV_CHANGED,
            old.captured_at, new.captured_at,
            f"中断超过 {ACOUSTIC_STALE_HOURS}h，声学环境需重新校准",
        ))

    # --- 10. 测试配置被修改 ---
    if old.test_config_hash and old.test_config_hash != new.test_config_hash:
        items.append(DriftItem(
            DriftType.TEST_CONFIG_MODIFIED,
            old.test_config_hash, new.test_config_hash,
            "测试配置（模型/设备/阈值）被修改",
        ))

    return DriftDetectionResult(
        detected=len(items) > 0,
        items=items,
        severity=_classify_severity(items),
    )