"""
P5 单测（预算管控）：RunBudget token 硬闸 + 墙钟止损；Coordinator 越界诚实收尾。

覆盖：
1. RunBudget 纯逻辑：add_usage/exceeded/reason/status 各分支；默认不设限（回归保护）。
2. 端到端：注入极小 max_total_tokens → analyze 前越界 → 跳过后续 LLM 阶段、
   stage_errors 结构化记录、报告如实标注提前终止。
3. 未设预算 → 行为与升级前一致（强回归保护）。
"""

from __future__ import annotations

from types import SimpleNamespace

import core.plugins.acoustic  # noqa: F401
import core.plugins.lamp      # noqa: F401
from core.agents import Coordinator
from core.agents.subagents import DECISION_TOOL_NAME
from core.observability import RunBudget
from core.plugins import default_registry


def _plugin():
    return default_registry.get("acoustic_lamp")


# ---------- 1. RunBudget 纯逻辑 ----------

def test_budget_unlimited_by_default():
    """默认两个上限都 None → 永不越界（旧行为）。"""
    b = RunBudget()
    b.start()
    b.add_usage(10_000_000, 10_000_000)
    assert b.exceeded() is False
    assert b.reason() == ""


def test_budget_token_exceeded():
    b = RunBudget(max_total_tokens=100)
    b.start()
    b.add_usage(60, 30)  # 90 < 100
    assert b.exceeded() is False
    b.add_usage(10, 5)   # 105 >= 100
    assert b.exceeded() is True
    assert b.reason() == "token"


def test_budget_wall_clock_exceeded():
    """墙钟上限设 0 → start 后立即越界，归因 wall_clock。"""
    b = RunBudget(max_wall_clock_s=0.0)
    b.start()
    # max_wall_clock_s=0.0 是「不设限」还是「立即越界」？按实现 None 才是不设限，
    # 0.0 为有限上限：elapsed>=0 恒成立 → 越界。
    assert b.reason() == "wall_clock"
    assert b.exceeded() is True


def test_budget_token_takes_priority_over_wall_clock():
    """两维同时越界时，token 优先归因（更硬的成本面）。"""
    b = RunBudget(max_total_tokens=10, max_wall_clock_s=0.0)
    b.start()
    b.add_usage(20, 0)
    assert b.reason() == "token"


def test_budget_status_fields():
    b = RunBudget(max_total_tokens=200)
    b.start()
    b.add_usage(50, 30)
    st = b.status()
    assert st["total_tokens"] == 80
    assert st["remaining_tokens"] == 120
    assert st["max_total_tokens"] == 200
    assert st["max_wall_clock_s"] is None
    assert st["exceeded"] is False


# ---------- 2/3. 端到端 ----------

def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, tool_input: dict, block_id: str = "tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _resp(content, stop_reason, in_tok=500, out_tok=200):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


class FakeMessages:
    def __init__(self, scripted):
        self._scripted = scripted
        self._i = 0
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        resp = self._scripted[min(self._i, len(self._scripted) - 1)]
        self._i += 1
        return resp


class FakeClient:
    def __init__(self, scripted):
        self.messages = FakeMessages(scripted)


def _wire(coord: Coordinator):
    """需求阶段消耗 700 token（500+200）→ 下一个 LLM 阶段前即越界。"""
    coord.requirement_agent.agent.client = FakeClient([
        _resp([_text_block('{"device":"台灯","test_type":"唤醒","metrics":[],"acceptance":""}')],
              "end_turn")])
    analysis_client = FakeClient([
        _resp([_text_block("分层定位完成。"),
               _tool_use_block(DECISION_TOOL_NAME,
                               {"need_reproduce": False, "is_new_bug": False,
                                "confidence": "high", "evidence_sufficiency": "sufficient"})],
              "tool_use")])
    report_client = FakeClient([
        _resp([_text_block("## 测试报告\n结论见上。")], "end_turn")])
    coord.analysis_agent.agent.client = analysis_client
    coord.report_agent.agent.client = report_client
    return analysis_client, report_client


def test_budget_abort_skips_downstream_llm_stages():
    """极小 token 预算：需求阶段后即越界 → analyze/report 的 LLM 调用被跳过，报告标注止损。"""
    # 预算 100 token，但需求阶段一次就花 700 → analyze 前必越界
    budget = RunBudget(max_total_tokens=100)
    coord = Coordinator(_plugin(), budget=budget)
    analysis_client, report_client = _wire(coord)

    events: list[tuple] = []
    report = coord.run("测试台灯唤醒率", on_event=lambda k, p: events.append((k, p)))

    # analyze/report 阶段的 LLM 未被调用
    assert analysis_client.messages.calls == 0
    assert report_client.messages.calls == 0
    # 越界事件 + 跳过事件
    assert any(k == "budget_exceeded" for k, _ in events)
    assert any(k == "stage_skipped" for k, _ in events)
    # stage_errors 结构化记录
    assert any("预算越界提前终止" in e for e in report.stage_errors)
    # 报告如实标注提前终止 + 诊断为空
    assert "提前终止" in report.report_md
    assert report.diagnosis is None


def test_no_budget_runs_fully():
    """未设预算（默认不设限）→ 全流程照常跑完，行为与升级前一致（强回归保护）。"""
    coord = Coordinator(_plugin())  # 无 budget，config 未设环境变量 → 不设限
    analysis_client, report_client = _wire(coord)
    report = coord.run("测试台灯唤醒率")

    assert analysis_client.messages.calls >= 1  # 分析阶段正常执行
    assert report_client.messages.calls >= 1    # 报告阶段正常执行
    assert report.diagnosis is not None
    assert "提前终止" not in report.report_md
