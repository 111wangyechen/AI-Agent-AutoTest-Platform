"""
P9 单测：收敛闭环 & 复测停滞检测。

把复测循环的停机准则从「唯一硬上限 MAX_REPRODUCE_ROUNDS」升级为三类并列：
证据充分（P8）/ 证据无增量（P9 停滞）/ 预算越界（P5）。本文件聚焦 P9 停滞这路。

分两层：
1. 纯逻辑：_evidence_fingerprint（相同结构稳定相等、新 collector 则不等）、
   _reproduce_should_stop（budget > stall > sufficient > 继续 的优先级）。
2. 端到端（假 client + 假 executor）：
   - 每轮复测返回「同结构」证据 → 指纹不变 → 在硬上限前因 stall 提前跳出、
     报告标注收敛止损、needs_human_review=True。
   - 每轮返回「新 collector」证据 → 指纹变 → 正常复测到硬上限，不误判停滞。
"""

from __future__ import annotations

from types import SimpleNamespace

import core.plugins.acoustic  # noqa: F401
import core.plugins.lamp      # noqa: F401
from core.agents import Coordinator
from core.agents.coordinator import MAX_REPRODUCE_ROUNDS
from core.agents.subagents import DECISION_TOOL_NAME
from core.observability import RunBudget
from core.plugins import Diagnosis, RawData, default_registry


def _plugin():
    return default_registry.get("acoustic_lamp")


def _coord():
    return Coordinator(_plugin())


# ---------- 1. _evidence_fingerprint 纯逻辑 ----------

def test_fingerprint_stable_for_same_structure():
    """同 collector + 同结构、仅具体数值不同 → 指纹相等（忽略值噪声）。"""
    coord = _coord()
    raw1 = [RawData("a", "logcat", {"status": "success", "result": {"lines": "X"}})]
    raw2 = [RawData("a", "logcat", {"status": "success", "result": {"lines": "Y"}})]
    assert coord._evidence_fingerprint(raw1) == coord._evidence_fingerprint(raw2)


def test_fingerprint_changes_on_new_collector():
    """新增一类 collector → 指纹改变（采到了新种类证据）。"""
    coord = _coord()
    raw1 = [RawData("a", "logcat", {"status": "success"})]
    raw2 = raw1 + [RawData("b", "audio_capture", {"status": "success"})]
    assert coord._evidence_fingerprint(raw1) != coord._evidence_fingerprint(raw2)


def test_fingerprint_changes_on_new_result_keys():
    """payload 结构新增键（如多了日志签名字段）→ 指纹改变。"""
    coord = _coord()
    raw1 = [RawData("a", "logcat", {"status": "success", "result": {"lines": "X"}})]
    raw2 = [RawData("a", "logcat",
                    {"status": "success", "result": {"lines": "X", "vad_end": 1}})]
    assert coord._evidence_fingerprint(raw1) != coord._evidence_fingerprint(raw2)


# ---------- 2. _reproduce_should_stop 优先级 ----------

def test_should_stop_stall_when_fp_equal():
    coord = _coord()
    fp = (frozenset({"x"}), frozenset())
    diag = Diagnosis(text="x", need_reproduce=True)
    assert coord._reproduce_should_stop(fp, fp, diag) == (True, "stall")


def test_should_stop_sufficient_when_model_done():
    coord = _coord()
    fp1 = (frozenset({"x"}), frozenset())
    fp2 = (frozenset({"y"}), frozenset())
    diag = Diagnosis(text="x", need_reproduce=False)
    assert coord._reproduce_should_stop(fp1, fp2, diag) == (True, "sufficient")


def test_should_continue_when_new_evidence_and_need_reproduce():
    coord = _coord()
    fp1 = (frozenset({"x"}), frozenset())
    fp2 = (frozenset({"y"}), frozenset())
    diag = Diagnosis(text="x", need_reproduce=True)
    assert coord._reproduce_should_stop(fp1, fp2, diag) == (False, "")


def test_should_stop_budget_takes_priority_over_stall():
    """预算越界优先于停滞：烧超了立刻停，归因 budget 而非 stall。"""
    b = RunBudget(max_total_tokens=1)
    b.start()
    b.add_usage(5, 0)
    coord = Coordinator(_plugin(), budget=b)
    fp = (frozenset({"x"}), frozenset())
    diag = Diagnosis(text="x", need_reproduce=True)
    assert coord._reproduce_should_stop(fp, fp, diag) == (True, "budget")


# ---------- 端到端脚手架 ----------

def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, tool_input: dict, block_id: str = "tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def _resp(content, stop_reason):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=100, output_tokens=30),
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


def _wire_non_analysis(coord: Coordinator):
    """需求 + 报告阶段用假 client（不走真实 API）。"""
    coord.requirement_agent.agent.client = FakeClient([
        _resp([_text_block('{"device":"台灯","test_type":"唤醒","metrics":[],"acceptance":""}')],
              "end_turn")])
    coord.report_agent.agent.client = FakeClient([
        _resp([_text_block("## 测试报告\n结论见上。")], "end_turn")])


def _always_reproduce_decision():
    """分析 Agent 每轮都要求复测；medium/sufficient 使 P8 门槛本身不触发复核，
    从而把 needs_human_review 是否为 True 完全归因于 P9 停滞逻辑。"""
    return {
        "need_reproduce": True,
        "reproduce_hint": "再采一轮",
        "is_new_bug": False,
        "confidence": "medium",
        "evidence_sufficiency": "sufficient",
    }


# ---------- 3. 端到端：停滞提前跳出 ----------

def test_stall_detection_stops_before_cap():
    """每轮复测返回同结构证据 → 指纹不变 → 在硬上限前因 stall 提前跳出。"""
    coord = _coord()
    _wire_non_analysis(coord)
    coord.analysis_agent.agent.client = FakeClient([
        _resp([_text_block("需要复测补证。"),
               _tool_use_block(DECISION_TOOL_NAME, _always_reproduce_decision())],
              "tool_use")])

    # 初始执行证据：logcat success
    initial = [RawData("c1", "logcat", {"status": "success", "result": {"lines": "L"}})]
    coord.executor_agent.run = lambda cases, on_event=None: list(initial)

    repro_calls = {"n": 0}

    def fake_reproduce(cases, diagnosis, on_event=None):
        repro_calls["n"] += 1
        # 同 collector/同结构（仅值不同）→ 指纹不变 → 触发停滞
        return [RawData("c1-r", "logcat", {"status": "success", "result": {"lines": "other"}})]

    coord.executor_agent.reproduce = fake_reproduce

    events: list[tuple] = []
    report = coord.run("测试台灯唤醒率", on_event=lambda k, p: events.append((k, p)))

    # 第一轮复测后即停滞跳出，未跑到硬上限
    assert repro_calls["n"] == 1
    assert repro_calls["n"] < MAX_REPRODUCE_ROUNDS + 1
    assert any(k == "reproduce_stall" for k, _ in events)
    # 停滞收敛 → 强制转人工 + 报告标注 + 结构化 stage_errors
    assert report.diagnosis.needs_human_review is True
    assert "复测收敛止损" in report.report_md
    assert any("复测收敛止损[停滞]" in e for e in report.stage_errors)


# ---------- 4. 端到端：持续带来新证据则不误判停滞 ----------

def test_new_evidence_runs_to_cap_without_false_stall():
    """每轮复测带来新 collector → 指纹持续变化 → 不误判停滞，正常复测到硬上限。"""
    coord = _coord()
    _wire_non_analysis(coord)
    coord.analysis_agent.agent.client = FakeClient([
        _resp([_text_block("需要复测补证。"),
               _tool_use_block(DECISION_TOOL_NAME, _always_reproduce_decision())],
              "tool_use")])

    coord.executor_agent.run = lambda cases, on_event=None: [
        RawData("c0", "logcat", {"status": "success", "result": {"lines": "L"}})]

    repro_calls = {"n": 0}
    new_collectors = ["audio_capture", "serial_log", "sensor_dump"]

    def fake_reproduce(cases, diagnosis, on_event=None):
        i = repro_calls["n"]
        repro_calls["n"] += 1
        return [RawData(f"c{i}-r", new_collectors[i],
                        {"status": "success", "result": {"k": "v"}})]

    coord.executor_agent.reproduce = fake_reproduce

    events: list[tuple] = []
    report = coord.run("测试台灯唤醒率", on_event=lambda k, p: events.append((k, p)))

    # 复测跑满硬上限（不被误判停滞）
    assert repro_calls["n"] == MAX_REPRODUCE_ROUNDS
    assert not any(k == "reproduce_stall" for k, _ in events)
    assert "复测收敛止损" not in report.report_md
    # 无停滞、P8 门槛也不触发（medium/sufficient）→ 不转人工
    assert report.diagnosis.needs_human_review is False
