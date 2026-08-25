"""插件契约单测：两插件都完整实现契约、可被注册表取用、工具可 mock 执行。"""

import pytest

import core.plugins.acoustic  # noqa: F401  触发注册（含 acoustic_lamp 设备插件）
import core.plugins.lamp      # noqa: F401
from core.plugins import (
    Checkpoint,
    FaultPattern,
    MetricDef,
    TestPlugin,
    TestRequirement,
    default_registry,
)
from core.tools import Tool, ToolRegistry


# 声学域重构后按设备拆分：acoustic_lamp(AI台灯) + lamp(WiFi/通话)
ALL_PLUGIN_NAMES = ["acoustic_lamp", "lamp"]


def test_both_plugins_registered():
    for name in ALL_PLUGIN_NAMES:
        assert name in default_registry.names()


@pytest.mark.parametrize("name", ALL_PLUGIN_NAMES)
def test_plugin_implements_contract(name):
    p = default_registry.get(name)
    assert isinstance(p, TestPlugin)
    assert p.name and p.device_desc
    # 5 扩展点都返回正确类型
    assert all(isinstance(t, Tool) for t in p.collectors())
    assert all(isinstance(t, Tool) for t in p.analysis_tools())
    assert all(isinstance(c, Checkpoint) for c in p.checkpoints())
    assert all(isinstance(f, FaultPattern) for f in p.knowledge_base())
    assert all(isinstance(m, MetricDef) for m in p.metrics())
    assert isinstance(p.analysis_prompt(), str) and p.analysis_prompt()
    assert isinstance(p.report_template(), str) and p.report_template()


@pytest.mark.parametrize("name", ALL_PLUGIN_NAMES)
def test_plugin_generates_cases(name):
    p = default_registry.get(name)
    cases = p.generate_cases(TestRequirement(raw="测试"))
    assert len(cases) >= 1
    assert all(c.case_id and c.title for c in cases)


@pytest.mark.parametrize("name", ALL_PLUGIN_NAMES)
def test_plugin_collectors_execute_in_mock(name):
    """每个采集器在 mock 模式下都能跑出结构化结果（含 status）。"""
    p = default_registry.get(name)
    reg = ToolRegistry(p.collectors())
    for case in p.generate_cases(TestRequirement(raw="测试")):
        collector = case.params.get("collector")
        if not collector:
            continue
        r = reg.execute(collector, case.params.get("collector_args", {}))
        assert "status" in r


def test_registry_unknown_plugin():
    with pytest.raises(KeyError):
        default_registry.get("does_not_exist")


def test_acoustic_has_7_checkpoints():
    """声学链路 7 层检查点，重构后由 acoustic_lamp 继承自 AcousticPluginBase。"""
    p = default_registry.get("acoustic_lamp")
    assert len(p.checkpoints()) == 7
