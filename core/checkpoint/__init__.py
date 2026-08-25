"""
通用内核 · Checkpoint / Resume 机制

长流程测试（采集→分析→报告）可能因网络抖动、设备掉线、进程崩溃中断。
本模块提供断点续跑能力，并在 resume 前由 Agent 驱动做「环境漂移检测」——
测试恢复时重新抓取设备/workspace/网络状态，和 checkpoint 时的锚点逐项对比，
判断环境是否已漂移，避免 Agent 基于过时假设做错决策。

模块划分：
- models.py       TestCheckpoint / EnvironmentSnapshot / DriftType 等数据结构
- store.py        CheckpointStore：原子化 JSON 存取、latest 指针、保留最近 N 个
- environment.py  capture_current_env()：adb 抓设备状态 + workspace hash + 网络
- drift.py        detect_drift()：10 场景逐项对比 + 三级严重程度
- preflight_agent.py  PreflightAgent：LLM 驱动的漂移研判与决策
"""

from __future__ import annotations

from .models import (
    DriftDecision,
    DriftDetectionResult,
    DriftSeverity,
    DriftType,
    EnvironmentSnapshot,
    TestCheckpoint,
)
from .store import CheckpointStore
from .environment import capture_current_env
from .drift import detect_drift
from .preflight_agent import PreflightAgent

__all__ = [
    "TestCheckpoint",
    "EnvironmentSnapshot",
    "DriftType",
    "DriftSeverity",
    "DriftDetectionResult",
    "DriftDecision",
    "CheckpointStore",
    "capture_current_env",
    "detect_drift",
    "PreflightAgent",
]
