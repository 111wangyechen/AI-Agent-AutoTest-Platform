"""
P0-A 单测：AnalysisAgent 的复测/记忆库决策改用 FinalTool 强制工具调用，
不再从 Markdown 尾部正则抠 JSON。

用假 client 模拟 Anthropic 响应，绕开真实 API。覆盖：
1. 模型收尾即调用结论工具 -> Diagnosis 各字段正确填充
2. 先多轮取证、再提交决策 -> tool_calls 有记录，决策仍被捕获
3. 模型始终不调用结论工具 -> 优雅降级，不崩，取安全默认
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.plugins.acoustic  # noqa: F401  触发插件注册
import core.plugins.lamp      # noqa: F401
from core.agents.subagents import AnalysisAgent, DECISION_TOOL_NAME
from core.plugins import default_registry


# ---------- 假 Anthropic 响应构件 ----------

def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, tool_input: dict, block_id: str = "tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _resp(content: list, stop_reason: str):
    return SimpleNamespace(content=content, stop_reason=stop_reason)


class FakeMessages:
    def __init__(self, scripted: list):
        self._scripted = scripted
        self._i = 0
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        resp = self._scripted[min(self._i, len(self._scripted) - 1)]
        self._i += 1
        return resp


class FakeClient:
    def __init__(self, scripted: list):
        self.messages = FakeMessages(scripted)


def _plugin():
    return default_registry.get("acoustic_lamp")


# ---------- 用例 ----------

def test_decision_captured_from_final_tool():
    """模型收尾直接调用结论工具，字段被正确解析到 Diagnosis。"""
    decision = {
        "need_reproduce": True,
        "reproduce_hint": "连续播放 20 次唤醒音频并抓四路音频",
        "target_evidence": ["four_channel_audio", "full_serial_log"],
        "matched_bad_cases": ["BC-001"],
        "is_new_bug": True,
        "bug_summary": {"title": "唤醒率不达标", "layer": "应用层"},
    }
    scripted = [
        _resp(
            [
                _text_block("已完成分层定位，唤醒率偏低，定位到应用层。"),
                _tool_use_block(DECISION_TOOL_NAME, decision),
            ],
            stop_reason="tool_use",
        ),
    ]
    agent = AnalysisAgent(_plugin(), client=FakeClient(scripted))
    diag = agent.run("分析唤醒率问题")

    assert diag.need_reproduce is True
    assert diag.reproduce_hint.startswith("连续播放")
    assert diag.target_evidence == ["four_channel_audio", "full_serial_log"]
    assert diag.matched_bad_cases == ["BC-001"]
    assert diag.is_new_bug is True
    assert diag.bug_summary["layer"] == "应用层"
    assert "应用层" in diag.text


def test_decision_after_investigation_rounds():
    """先调取证工具、再提交结论工具；tool_calls 记录取证、决策仍被捕获。"""
    # 取证工具名取插件第一个 analysis 工具
    plugin = _plugin()
    evidence_tool = plugin.analysis_tools()[0].name

    round1 = _resp(
        [
            _text_block("先抓一次日志看看。"),
            _tool_use_block(evidence_tool, {}, block_id="tu_eviden"),
        ],
        stop_reason="tool_use",
    )
    decision = {"need_reproduce": False, "is_new_bug": False}
    round2 = _resp(
        [
            _text_block("证据充分，无需复测。"),
            _tool_use_block(DECISION_TOOL_NAME, decision, block_id="tu_dec"),
        ],
        stop_reason="tool_use",
    )
    agent = AnalysisAgent(plugin, client=FakeClient([round1, round2]))
    diag = agent.run("分析")

    assert diag.need_reproduce is False
    assert diag.is_new_bug is False
    # 取证工具被执行并记录，结论工具不进 tool_calls_log
    assert any(c["name"] == evidence_tool for c in diag.tool_calls)
    assert all(c["name"] != DECISION_TOOL_NAME for c in diag.tool_calls)
    assert diag.rounds >= 2


def test_graceful_degradation_when_no_decision_tool():
    """模型始终不调用结论工具（纯文本收尾）-> 不崩，取安全默认。"""
    scripted = [
        _resp([_text_block("这是一段没有结构化决策的结论。")], stop_reason="end_turn"),
    ]
    agent = AnalysisAgent(_plugin(), client=FakeClient(scripted))
    diag = agent.run("分析")

    assert diag.need_reproduce is False
    assert diag.is_new_bug is False
    assert diag.matched_bad_cases == []
    assert diag.bug_summary == {}
    assert "没有结构化决策" in diag.text
