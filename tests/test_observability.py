"""
P5 单测（可观测性）：Tracer 订阅事件流累积 token/耗时/工具调用，产出 trace.json。

分两层测：
1. Tracer 纯逻辑（喂事件 → summary/dump 正确）——不依赖 LLM。
2. 端到端：用假 client（带 usage）驱动 Coordinator 跑一次，断言 workspace 下
   生成 trace.json 且含 token/耗时/工具统计，报告附消耗概览。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import core.plugins.acoustic  # noqa: F401
import core.plugins.lamp      # noqa: F401
from core.agents import Coordinator
from core.agents.subagents import DECISION_TOOL_NAME
from core.observability import RunBudget, Tracer
from core.plugins import default_registry


def _plugin():
    return default_registry.get("acoustic_lamp")


# ---------- 1. Tracer 纯逻辑 ----------

def test_tracer_accumulates_llm_and_tool_calls():
    tr = Tracer()
    tr.on_event("llm_call", {"agent": "结果分析", "model": "m",
                             "input_tokens": 100, "output_tokens": 40,
                             "duration_s": 1.2, "round": 1})
    tr.on_event("llm_call", {"agent": "结果分析", "model": "m",
                             "input_tokens": 50, "output_tokens": 10,
                             "duration_s": 0.5, "round": 2})
    tr.on_event("tool_result", {"agent": "结果分析", "name": "logcat",
                                "duration_s": 0.3, "ok": True})
    tr.on_event("tool_result", {"agent": "结果分析", "name": "adb",
                                "duration_s": 0.1, "ok": False})
    s = tr.summary()
    assert s["total_input_tokens"] == 150
    assert s["total_output_tokens"] == 50
    assert s["total_tokens"] == 200
    assert s["llm_call_count"] == 2
    assert s["tool_call_count"] == 2
    assert s["tool_fail_count"] == 1
    assert s["by_agent"]["结果分析"]["llm_calls"] == 2
    assert s["by_agent"]["结果分析"]["input_tokens"] == 150


def test_tracer_dump_writes_trace_json():
    tr = Tracer()
    tr.on_event("llm_call", {"agent": "a", "model": "m",
                             "input_tokens": 10, "output_tokens": 5,
                             "duration_s": 0.2, "round": 1})
    with tempfile.TemporaryDirectory() as ws:
        p = Path(ws) / "trace.json"
        tr.dump(str(p))
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["summary"]["total_tokens"] == 15
        assert len(data["llm_calls"]) == 1
        assert data["llm_calls"][0]["agent"] == "a"


def test_tracer_feeds_budget_and_notifies_once():
    """Tracer 把 usage 喂给 budget，越界经 downstream 回放一次 budget_exceeded。"""
    seen: list[tuple] = []
    budget = RunBudget(max_total_tokens=100)
    budget.start()
    tr = Tracer(budget=budget, downstream=lambda k, p: seen.append((k, p)))
    # 第一次没越界
    tr.on_event("llm_call", {"input_tokens": 30, "output_tokens": 10})
    # 第二次越界（累计 100 >= 100）
    tr.on_event("llm_call", {"input_tokens": 40, "output_tokens": 20})
    # 第三次仍越界，但只应通知一次
    tr.on_event("llm_call", {"input_tokens": 10, "output_tokens": 0})
    exceeded_events = [k for k, _ in seen if k == "budget_exceeded"]
    assert len(exceeded_events) == 1
    assert seen[0][0] == "budget_exceeded"  # downstream 只在越界时收到这类事件
    assert seen[0][1]["reason"] == "token"


# ---------- 2. 端到端 trace.json ----------

def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, tool_input: dict, block_id: str = "tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _resp(content, stop_reason, in_tok=120, out_tok=30):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


class FakeMessages:
    def __init__(self, scripted):
        self._scripted = scripted
        self._i = 0

    def create(self, **kwargs):
        resp = self._scripted[min(self._i, len(self._scripted) - 1)]
        self._i += 1
        return resp


class FakeClient:
    def __init__(self, scripted):
        self.messages = FakeMessages(scripted)


def _wire_fake_clients(coord: Coordinator):
    coord.requirement_agent.agent.client = FakeClient([
        _resp([_text_block('{"device":"台灯","test_type":"唤醒","metrics":[],"acceptance":""}')],
              "end_turn")])
    coord.analysis_agent.agent.client = FakeClient([
        _resp([_text_block("分层定位完成。"),
               _tool_use_block(DECISION_TOOL_NAME,
                               {"need_reproduce": False, "is_new_bug": False,
                                "confidence": "high", "evidence_sufficiency": "sufficient"})],
              "tool_use")])
    coord.report_agent.agent.client = FakeClient([
        _resp([_text_block("## 测试报告\n结论见上。")], "end_turn")])


def test_pipeline_generates_trace_json():
    """配 workspace 跑一次 → trace.json 生成且含 token/耗时统计，报告附消耗概览。"""
    with tempfile.TemporaryDirectory() as ws:
        coord = Coordinator(_plugin(), workspace_path=ws)
        _wire_fake_clients(coord)
        report = coord.run("测试台灯唤醒率")

        trace_file = Path(ws) / "trace.json"
        assert trace_file.exists()
        data = json.loads(trace_file.read_text(encoding="utf-8"))
        assert data["summary"]["llm_call_count"] >= 1
        assert data["summary"]["total_tokens"] > 0
        # 报告末尾附消耗概览
        assert "本次测试消耗概览" in report.report_md


def test_pipeline_without_workspace_no_trace_file():
    """未配 workspace → 不落 trace.json（旧行为），但报告仍附消耗概览。"""
    coord = Coordinator(_plugin())  # 无 workspace
    _wire_fake_clients(coord)
    report = coord.run("测试台灯唤醒率")
    assert "本次测试消耗概览" in report.report_md
