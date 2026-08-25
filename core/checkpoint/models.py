"""
Checkpoint / 漂移检测 · 数据结构

命名说明：叫 TestCheckpoint 是为了和 core.plugins.models.Checkpoint（链路检查点，
如「唤醒触发」）区分——那个是声学链路上的一个观测点，这个是测试执行的断点快照。

全部用 stdlib dataclass + to_dict/from_dict 手写序列化（不引 pydantic），
符合项目「极简依赖」约定，也让 JSON 落盘格式完全可控。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ========== 漂移类型（10 个设计枚举场景）==========

class DriftType(str, Enum):
    """resume 时可能发生的 10 种环境漂移场景，分三类。"""

    # --- 设备状态变化（4）---
    DEVICE_VERSION_CHANGED = "设备ROM版本更新"
    DEVICE_REBOOT = "设备重启导致日志/缓存清空"
    DEVICE_IP_CHANGED = "设备掉线重连后IP变化"
    DEVICE_HARDWARE_CHANGED = "设备硬件状态超基线(电量/温度)"

    # --- workspace 文件变化（3）---
    WORKSPACE_FILES_MODIFIED = "workspace文件被外部删除/修改"
    AUDIO_FILE_CORRUPTED = "已采集音频文件损坏"
    CHECKPOINT_TAMPERED = "checkpoint文件被篡改"

    # --- 测试环境变化（3）---
    NETWORK_DEGRADED = "网络从通畅变弱网"
    ACOUSTIC_ENV_CHANGED = "实验室声学环境变化(底噪)"
    TEST_CONFIG_MODIFIED = "测试配置被修改"


class DriftSeverity(str, Enum):
    """漂移严重程度，决定响应策略。"""

    NONE = "NONE"      # 无漂移
    LOW = "LOW"        # 轻度：标注存疑，继续
    MEDIUM = "MEDIUM"  # 中度：丢弃 checkpoint，重跑当前阶段
    HIGH = "HIGH"      # 重度：报警转人工


class DriftDecision(str, Enum):
    """漂移研判后的决策动作。"""

    CONTINUE = "CONTINUE"          # 继续 resume
    CONTINUE_FLAGGED = "CONTINUE_FLAGGED"  # 继续但标注结果存疑
    RERUN_STAGE = "RERUN_STAGE"    # 重跑当前阶段
    ABORT_TO_HUMAN = "ABORT_TO_HUMAN"      # 转人工


# ========== 环境快照（漂移检测锚点）==========

@dataclass
class EnvironmentSnapshot:
    """一次环境快照：checkpoint 存它，resume 时重新抓一份来对比。"""

    # 设备状态（对应漂移场景 1-4）
    device_rom_version: str = ""   # getprop / build.prop 里的版本
    device_serial: str = ""        # 设备序列号
    device_ip: str = ""            # 设备 IP（检测掉线重连后是否变化）
    device_uptime_s: float = 0.0   # /proc/uptime，用于检测重启
    device_baseline: dict[str, Any] = field(default_factory=dict)  # cpu/mem/temp

    # workspace 状态（对应漂移场景 5-7）
    workspace_hash: str = ""       # workspace 目录 merkle hash
    collected_files: list[dict[str, str]] = field(default_factory=list)  # [{path, sha256}]
    checkpoint_integrity: str = ""  # checkpoint 文件自身校验和

    # 测试环境（对应漂移场景 8-10）
    network_latency_ms: int = -1   # ping 设备延迟（-1 表示未测/不可达）
    cloud_api_latency_ms: int = -1  # 云端 API 延迟
    test_config_hash: str = ""     # 测试配置文件 hash

    # 采集时间（用于声学环境漂移的时间启发式判断）
    captured_at: str = ""          # ISO 时间戳

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EnvironmentSnapshot":
        known = {f for f in cls.__dataclass_fields__}  # noqa
        return cls(**{k: v for k, v in d.items() if k in known})


# ========== 漂移检测结果 ==========

@dataclass
class DriftItem:
    """单项漂移：哪个场景、锚点旧值 vs 当前值。"""

    drift_type: DriftType
    old_value: str = ""
    new_value: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "drift_type": self.drift_type.value,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "note": self.note,
        }


@dataclass
class DriftDetectionResult:
    """detect_drift 的产出：确定性逐项对比结果，喂给 PreflightAgent 研判。"""

    detected: bool = False
    items: list[DriftItem] = field(default_factory=list)
    severity: DriftSeverity = DriftSeverity.NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "severity": self.severity.value,
            "items": [it.to_dict() for it in self.items],
        }

    def summary(self) -> str:
        """人类/LLM 可读的一句话摘要。"""
        if not self.detected:
            return "环境一致，未检测到漂移。"
        types = "、".join(it.drift_type.value for it in self.items)
        return f"检测到 {len(self.items)} 项漂移（严重程度 {self.severity.value}）：{types}"


# ========== 测试断点快照 ==========

@dataclass
class TestCheckpoint:
    """测试执行的断点快照。resume 时读它重建任务状态。"""

    # 执行进度索引
    checkpoint_id: str             # 时间戳命名，如 ckpt_20260824_100523
    test_id: str = ""              # 测试任务 ID
    plugin: str = ""               # 用哪个插件（acoustic_lamp 等）
    current_stage: str = ""        # requirement/case_gen/execute/analyze/report
    completed_stages: list[str] = field(default_factory=list)
    current_case_index: int = 0    # 执行阶段：跑到第几条用例
    total_cases: int = 0

    # 关键中间产物（存路径/结构化小对象，不存 agent 上下文）
    artifacts: dict[str, Any] = field(default_factory=dict)

    # 环境快照（漂移检测锚点）
    env_snapshot: EnvironmentSnapshot = field(default_factory=EnvironmentSnapshot)

    # 元数据
    created_at: str = ""
    workspace_path: str = ""

    def done(self, stage: str) -> bool:
        """resume 时判断某阶段是否已完成（单测 test_resume_skips_done_stages 用）。"""
        return stage in self.completed_stages

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["env_snapshot"] = self.env_snapshot.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TestCheckpoint":
        d = dict(d)
        env = d.pop("env_snapshot", {})
        known = {f for f in cls.__dataclass_fields__}  # noqa
        obj = cls(**{k: v for k, v in d.items() if k in known})
        obj.env_snapshot = EnvironmentSnapshot.from_dict(env or {})
        return obj
