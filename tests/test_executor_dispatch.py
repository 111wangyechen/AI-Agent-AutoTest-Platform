"""
执行 Agent · 采集器派发单测

回归重点（真实踩过的坑）：
执行阶段曾用「工具名是否含 playback/log 子串」来找工具。台灯插件把播放工具
改名为 lamp_wakeup_test 后，'playback' 子串匹配不上 → 执行阶段静默跳过播放、
只读日志 → 表面「测试跑完了」，实际没放音频、设备根本没被唤醒。

现在改为按角色向插件问（audio_playback_tool/log_collector/audio_collector），
本文件锁死这个行为：改工具名不能再让播放步骤消失。
"""

import pytest

import core.plugins.acoustic.lamp_plugin  # noqa: F401  触发注册
from core.agents import ExecutorAgent
from core.plugins import default_registry
from core.plugins.models import TestCase

AUDIO = r"D:\LL20\唤醒音频-小白-300次.mp3"


@pytest.fixture
def executor():
    return ExecutorAgent(default_registry.get("acoustic_lamp"))


def _case(**collector_args):
    return TestCase(
        case_id="LAMP-WK-01",
        title="唤醒测试",
        params={"collector_args": collector_args},
    )


# ---------- 角色解析 ----------

def test_role_tools_resolve_regardless_of_tool_name(executor):
    """三个角色都能解析到工具名——不依赖名字里含什么子串。"""
    assert executor._role_tool("playback") == "lamp_wakeup_test"
    assert executor._role_tool("log") == "lamp_log_from_storage"
    assert executor._role_tool("audio") == "lamp_audio_capture_exclusive"


def test_unknown_role_returns_none(executor):
    assert executor._role_tool("does_not_exist") is None


# ---------- 派发流程 ----------

def test_playback_is_dispatched(executor):
    """核心回归：播放步骤必须真的被派发（曾因名字匹配失败被静默跳过）。"""
    dispatched = []
    executor.run(
        [_case(audio_file=AUDIO, expected_count=300)],
        on_event=lambda kind, p: dispatched.append(p.get("collector")) if kind == "exec" else None,
    )
    assert "lamp_wakeup_test" in dispatched, "播放步骤被跳过了——正是那个静默失败的 bug"
    assert "lamp_log_from_storage" in dispatched


def test_expected_count_passed_through(executor):
    """expected_count 要透传给播放工具，否则唤醒率算错分母。"""
    raw = executor.run([_case(audio_file=AUDIO, expected_count=300)])
    play = next(r for r in raw if r.collector == "lamp_wakeup_test")
    assert play.payload["summary"]["expected_count"] == 300


def test_missing_audio_file_reports_error_not_silence(executor):
    """
    用例没给 audio_file 时，要留下显式错误记录，而不是静默跳过。
    静默跳过 = 「跑完了但没声音」这类问题最难查。
    """
    raw = executor.run([_case(expected_count=300)])  # 故意不给 audio_file
    play = [r for r in raw if r.collector == "lamp_wakeup_test"]
    assert play, "即使缺参数也应留下一条记录"
    assert play[0].payload["status"] == "error"
    assert "audio_file" in play[0].payload["result"]


def test_audio_capture_skipped_by_default(executor):
    """四路音频独占 adb 且耗时长，默认不抓。"""
    raw = executor.run([_case(audio_file=AUDIO, expected_count=300)])
    assert not [r for r in raw if r.collector == "lamp_audio_capture_exclusive"]


def test_audio_capture_opt_in(executor):
    """显式 capture_audio=True 才抓四路音频。"""
    raw = executor.run([_case(audio_file=AUDIO, expected_count=10, capture_audio=True)])
    assert [r for r in raw if r.collector == "lamp_audio_capture_exclusive"]


def test_lamp_uses_sequential_audio_first_strategy():
    """台灯必须是串行策略——音频抓取独占 adb，不能并行采日志。"""
    assert default_registry.get("acoustic_lamp").execution_strategy() == "sequential_audio_first"
