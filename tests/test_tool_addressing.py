"""
P0-B 单测：工具寻址枚举化 + 结果归一化。

- ToolRole 成为一等公民，插件通过 tool_for_role() 声明角色→工具映射，
  取代早期方法名魔法字符串 + getattr 的脆弱寻址。
- normalize_result() 保证工具结果一定含 status 键，给 Agent 稳定错误面。
"""

from __future__ import annotations

import core.plugins.acoustic.lamp_plugin  # noqa: F401  触发注册
from core.agents import ExecutorAgent
from core.plugins import default_registry
from core.tools import ToolRole, normalize_result


def _plugin():
    return default_registry.get("acoustic_lamp")


# ---------- tool_for_role 契约 ----------

def test_tool_for_role_resolves_all_roles():
    p = _plugin()
    assert p.tool_for_role(ToolRole.PLAYBACK).name == "lamp_wakeup_test"
    assert p.tool_for_role(ToolRole.LOG).name == "lamp_log_from_storage"
    assert p.tool_for_role(ToolRole.AUDIO).name == "lamp_audio_capture_exclusive"


def test_executor_role_tool_accepts_enum_and_str():
    """_role_tool 同时接受 ToolRole 枚举和其字符串值，行为一致。"""
    ex = ExecutorAgent(_plugin())
    assert ex._role_tool(ToolRole.PLAYBACK) == "lamp_wakeup_test"
    assert ex._role_tool("playback") == "lamp_wakeup_test"
    assert ex._role_tool(ToolRole.LOG) == "lamp_log_from_storage"


def test_unknown_role_returns_none():
    ex = ExecutorAgent(_plugin())
    assert ex._role_tool("does_not_exist") is None


def test_role_resolves_regardless_of_tool_name():
    """回归锁：插件把工具改名，角色仍解析得到——正是当年静默失败的 bug。"""
    p = _plugin()
    # 工具真实名字带插件前缀，不含 'playback' 子串，仍能按角色解析
    tool = p.tool_for_role(ToolRole.PLAYBACK)
    assert tool is not None
    assert "playback" not in tool.name  # 名字里没有子串
    assert tool.name == "lamp_wakeup_test"


# ---------- normalize_result 归一 ----------

def test_normalize_keeps_dict_with_status():
    r = {"status": "success", "data": 1}
    assert normalize_result(r) is r  # 原样返回


def test_normalize_bare_error_gets_error_status():
    r = normalize_result({"error": "boom"})
    assert r["status"] == "error"
    assert r["error"] == "boom"


def test_normalize_dict_without_status_defaults_success():
    r = normalize_result({"lines": ["a", "b"]})
    assert r["status"] == "success"
    assert r["lines"] == ["a", "b"]


def test_normalize_non_dict_wrapped():
    r = normalize_result("plain text")
    assert r["status"] == "success"
    assert r["result"] == "plain text"
