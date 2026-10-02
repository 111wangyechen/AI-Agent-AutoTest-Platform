"""
P8 单测：证据置信度门槛 & 防假绿。

分析 Agent 的决策新增 confidence + evidence_sufficiency 两维，Coordinator 据此分流：
低置信 / 证据不足的结论不被当定论自动写库为 CONFIRMED，而是转人工复核、报告置顶横幅。

用假 client 模拟 Anthropic 响应，绕开真实 API。覆盖：
1. AnalysisAgent 从决策工具入参填 confidence/evidence_sufficiency；缺失时取安全默认（回归保护）
2. _gate_confidence 的四条分流分支（low / insufficient / contradictory / 正常）
3. Diagnosis 落盘读回含三个新字段（兼容旧文件）
4. _prepend_review_banner 确定性横幅（不依赖 LLM 自觉）
5. 端到端：高置信→Golden-set 落 CONFIRMED；低置信→只落 HYPOTHESIS + needs_human_review
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import core.plugins.acoustic  # noqa: F401  触发插件注册
import core.plugins.lamp      # noqa: F401
from core.agents import Coordinator
from core.agents.subagents import AnalysisAgent, DECISION_TOOL_NAME, DECISION_SCHEMA
from core.golden_set import BadCaseStatus, GoldenSet
from core.plugins import Diagnosis, default_registry


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


# ---------- 1. schema / AnalysisAgent 字段填充 ----------

def test_schema_has_two_confidence_dimensions():
    """DECISION_SCHEMA 增两 property，但 required 不变（旧兼容）。"""
    props = DECISION_SCHEMA["properties"]
    assert set(props["confidence"]["enum"]) == {"high", "medium", "low"}
    assert set(props["evidence_sufficiency"]["enum"]) == {"sufficient", "insufficient"}
    assert DECISION_SCHEMA["required"] == ["need_reproduce", "is_new_bug"]


def test_analysis_fills_confidence_fields():
    """模型在决策工具里给了两维 → Diagnosis 字段被正确填充。"""
    decision = {
        "need_reproduce": True,
        "is_new_bug": False,
        "confidence": "low",
        "evidence_sufficiency": "insufficient",
    }
    scripted = [_resp(
        [_text_block("证据零散，暂难定论。"), _tool_use_block(DECISION_TOOL_NAME, decision)],
        stop_reason="tool_use",
    )]
    diag = AnalysisAgent(_plugin(), client=FakeClient(scripted)).run("分析")
    assert diag.confidence == "low"
    assert diag.evidence_sufficiency == "insufficient"


def test_analysis_defaults_when_fields_absent():
    """旧模型/降级没给两维 → 取安全默认 medium/sufficient（行为同升级前）。"""
    decision = {"need_reproduce": False, "is_new_bug": False}  # 无新字段
    scripted = [_resp(
        [_text_block("结论。"), _tool_use_block(DECISION_TOOL_NAME, decision)],
        stop_reason="tool_use",
    )]
    diag = AnalysisAgent(_plugin(), client=FakeClient(scripted)).run("分析")
    assert diag.confidence == "medium"
    assert diag.evidence_sufficiency == "sufficient"
    assert diag.needs_human_review is False


# ---------- 2. _gate_confidence 分流分支 ----------

def _coord():
    return Coordinator(_plugin())  # 无 workspace，纯逻辑测试


def test_gate_high_confidence_passes():
    """高置信 + 证据充分 → 不需复核。"""
    diag = Diagnosis(text="x", confidence="high", evidence_sufficiency="sufficient")
    _coord()._gate_confidence(diag)
    assert diag.needs_human_review is False


def test_gate_low_confidence_flags_review():
    """低置信 → 转人工复核。"""
    diag = Diagnosis(text="x", confidence="low", evidence_sufficiency="sufficient")
    _coord()._gate_confidence(diag)
    assert diag.needs_human_review is True


def test_gate_insufficient_evidence_flags_review():
    """证据不足 → 转人工复核。"""
    diag = Diagnosis(text="x", confidence="high",
                     evidence_sufficiency="insufficient", need_reproduce=True)
    _coord()._gate_confidence(diag)
    assert diag.needs_human_review is True


def test_gate_contradictory_signal_flags_review():
    """矛盾信号：说证据不足却又不复测 → 强制转人工，不许洗成通过。"""
    diag = Diagnosis(text="x", confidence="medium",
                     evidence_sufficiency="insufficient", need_reproduce=False)
    _coord()._gate_confidence(diag)
    assert diag.needs_human_review is True


def test_gate_none_is_safe():
    """diagnosis 为 None（分析阶段崩了）→ 不炸。"""
    _coord()._gate_confidence(None)  # 不抛异常即通过


def test_gate_emits_event():
    """标记复核时 emit human_review_flagged 事件。"""
    events: list[tuple] = []
    diag = Diagnosis(text="x", confidence="low")
    _coord()._gate_confidence(diag, on_event=lambda k, p: events.append((k, p)))
    assert any(k == "human_review_flagged" for k, _ in events)


# ---------- 3. 落盘读回往返（含新字段）----------

def test_diagnosis_save_load_roundtrip():
    """_save/_load_diagnosis 往返保留 confidence/evidence_sufficiency/needs_human_review。"""
    with tempfile.TemporaryDirectory() as ws:
        coord = Coordinator(_plugin(), workspace_path=ws)
        diag = Diagnosis(
            text="结论", confidence="low",
            evidence_sufficiency="insufficient", needs_human_review=True,
        )
        coord._save_diagnosis(diag)
        loaded = coord._load_diagnosis()
        assert loaded is not None
        assert loaded.confidence == "low"
        assert loaded.evidence_sufficiency == "insufficient"
        assert loaded.needs_human_review is True


def test_diagnosis_load_old_file_defaults():
    """旧 diagnosis.json 没有三个新键 → 读回取安全默认（强回归保护）。"""
    import json
    with tempfile.TemporaryDirectory() as ws:
        coord = Coordinator(_plugin(), workspace_path=ws)
        old_payload = {  # 升级前的字段集合
            "text": "旧结论", "rounds": 1, "tool_calls": [],
            "need_reproduce": False, "reproduce_hint": "", "target_evidence": [],
            "matched_bad_cases": [], "is_new_bug": False, "bug_summary": {},
        }
        (Path(ws) / "diagnosis.json").write_text(
            json.dumps(old_payload, ensure_ascii=False), encoding="utf-8")
        loaded = coord._load_diagnosis()
        assert loaded.confidence == "medium"
        assert loaded.evidence_sufficiency == "sufficient"
        assert loaded.needs_human_review is False


# ---------- 4. 确定性复核横幅 ----------

def test_review_banner_prepended_when_flagged():
    """needs_human_review=True → 报告顶部确定性插入复核横幅。"""
    diag = Diagnosis(text="x", confidence="low", needs_human_review=True)
    out = Coordinator._prepend_review_banner("## 测试报告\n正文", diag)
    assert out.startswith(">")
    assert "需人工复核" in out
    assert "## 测试报告" in out  # 原报告保留


def test_no_banner_when_not_flagged():
    """未标记复核 → 报告原样返回，不加横幅。"""
    diag = Diagnosis(text="x", confidence="high", needs_human_review=False)
    out = Coordinator._prepend_review_banner("## 测试报告", diag)
    assert out == "## 测试报告"


# ---------- 5. 端到端：Golden-set 落库状态分流 ----------

def _run_pipeline_with_decision(decision: dict, golden_root: str):
    """用假 client 驱动整条流水线跑到 Golden-set 写库，返回写入的 BadCase 列表。

    三个走 LLM 的子 Agent（需求/分析/报告）都注入假 client；用例设计与执行
    在 mock 模式下不依赖 LLM。GOLDEN_SET_ROOT 指到临时目录，避免污染真实库。
    """
    import os
    os.environ["GOLDEN_SET_ROOT"] = golden_root
    try:
        coord = Coordinator(_plugin())
        coord.requirement_agent.agent.client = FakeClient([
            _resp([_text_block('{"device":"台灯","test_type":"唤醒","metrics":[],"acceptance":""}')],
                  stop_reason="end_turn")])
        coord.analysis_agent.agent.client = FakeClient([
            _resp([_text_block("分层定位完成。"),
                   _tool_use_block(DECISION_TOOL_NAME, decision)], stop_reason="tool_use")])
        coord.report_agent.agent.client = FakeClient([
            _resp([_text_block("## 测试报告\n结论见上。")], stop_reason="end_turn")])

        report = coord.run("测试台灯唤醒率")
        written = GoldenSet(root_path=golden_root).store.load_all()
        return report, written
    finally:
        os.environ.pop("GOLDEN_SET_ROOT", None)


def _new_bug_decision(**overrides) -> dict:
    d = {
        "need_reproduce": False,
        "is_new_bug": True,
        "bug_summary": {
            "title": "唤醒率不达标", "symptom": "连续唤醒偶发吞唤醒",
            "root_cause": "应用层不应期误判", "layer": "应用层",
            "tags": ["唤醒"], "log_signatures": ["VAD_END"], "severity": "high",
        },
        "confidence": "high",
        "evidence_sufficiency": "sufficient",
    }
    d.update(overrides)
    return d


def test_golden_set_confirmed_when_high_confidence():
    """高置信 + 证据充分 + 新 bug → 落库为 CONFIRMED，不需复核。"""
    with tempfile.TemporaryDirectory() as gr:
        report, written = _run_pipeline_with_decision(_new_bug_decision(), gr)
        assert len(written) == 1
        assert written[0].status == BadCaseStatus.CONFIRMED
        assert report.diagnosis.needs_human_review is False


def test_golden_set_hypothesis_when_low_confidence():
    """低置信的新 bug → 仍留档，但只落 HYPOTHESIS、标记需人工复核、报告有横幅。"""
    with tempfile.TemporaryDirectory() as gr:
        report, written = _run_pipeline_with_decision(
            _new_bug_decision(confidence="low"), gr)
        assert len(written) == 1
        assert written[0].status == BadCaseStatus.HYPOTHESIS
        assert report.diagnosis.needs_human_review is True
        assert "需人工复核" in report.report_md


def test_golden_set_hypothesis_when_evidence_insufficient():
    """证据不足但模型不想复测（矛盾）→ 强制转人工、落 HYPOTHESIS。"""
    with tempfile.TemporaryDirectory() as gr:
        report, written = _run_pipeline_with_decision(
            _new_bug_decision(evidence_sufficiency="insufficient", need_reproduce=False), gr)
        assert len(written) == 1
        assert written[0].status == BadCaseStatus.HYPOTHESIS
        assert report.diagnosis.needs_human_review is True
