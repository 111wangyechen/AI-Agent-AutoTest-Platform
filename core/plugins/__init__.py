"""通用内核 · 插件系统：契约、数据模型、注册表。"""

from .base import TestPlugin
from .models import (
    Checkpoint,
    Diagnosis,
    FaultPattern,
    MetricDef,
    RawData,
    TestCase,
    TestReport,
    TestRequirement,
)
from .registry import PluginRegistry, default_registry

__all__ = [
    "TestPlugin",
    "Checkpoint",
    "Diagnosis",
    "FaultPattern",
    "MetricDef",
    "RawData",
    "TestCase",
    "TestReport",
    "TestRequirement",
    "PluginRegistry",
    "default_registry",
]
