"""
通用内核 · 数据模型

内核在 5 个子 Agent 之间流转的都是这些结构，与具体测试域无关。
声学插件也好、台灯插件也好，产出/消费的都是同一套 artifact，
这正是「换插件即换测试域、同一 run()」得以成立的前提。

用 stdlib dataclass（不引第三方），符合 KamaClaude 极简依赖约定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Checkpoint:
    """链路上的一个检查点（如声学的「唤醒触发」、WiFi 的「吞吐达标」）。"""

    step: int
    name: str
    layer: str  # 硬件 / 驱动 / 框架 / 应用 / 云端 / 网络
    tags: list[str] = field(default_factory=list)  # 该检查点对应的日志 tag/关键字


@dataclass
class FaultPattern:
    """一条故障模式知识：症状 → 根因 → 修复。构成插件的「故障知识库」。"""

    symptom: str          # 观察到的现象
    signature: str        # 日志/指标特征
    layer: str            # 故障层级
    root_cause: str       # 根本原因
    fix: str              # 修复建议


@dataclass
class MetricDef:
    """一个指标的定义与判据（如平均带宽 ≥ 20Mbps）。"""

    key: str
    label: str
    unit: str = ""
    threshold: str = ""   # 人类可读的验收判据，供分析 Agent 参考


@dataclass
class TestCase:
    """一条测试用例：参数组合 + 预期。由用例设计 Agent 产出。"""

    case_id: str
    title: str
    params: dict[str, Any] = field(default_factory=dict)
    expected: str = ""


# ---------- 段间流转的 artifact ----------

@dataclass
class TestRequirement:
    """需求分析 Agent 的产出：结构化测试需求。"""

    raw: str                                   # 原始自然语言需求
    device: str = ""
    test_type: str = ""
    metrics: list[str] = field(default_factory=list)
    acceptance: str = ""


@dataclass
class RawData:
    """执行 Agent 的产出：采集到的原始证据（日志/指标/工具输出）。"""

    case_id: str
    collector: str
    payload: dict[str, Any] = field(default_factory=dict)

    # checkpoint 持久化用：让 execute 阶段的产出能落盘、resume 时重建，
    # 否则 analyze/report 崩溃后 resume 拿不到数据、报告是空的。
    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "collector": self.collector, "payload": self.payload}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawData":
        return cls(
            case_id=d.get("case_id", ""),
            collector=d.get("collector", ""),
            payload=d.get("payload", {}),
        )


@dataclass
class Diagnosis:
    """结果分析 Agent 的产出：分层定位结论（核心价值）。"""

    text: str                                  # Markdown 结论
    rounds: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    # ---------- 复测决策（分析→执行 自主回退用）----------
    # 分析 Agent 认为证据不足以定论时，请求回到执行阶段自主复现并补采证据。
    need_reproduce: bool = False
    reproduce_hint: str = ""                   # 给执行阶段的复现指导（怎么触发、抓什么）
    target_evidence: list[str] = field(default_factory=list)  # 需要补采的证据类型
    # 如 ["four_channel_audio", "full_serial_log", "vad_end_timing"]

    # ---------- Golden-set 关联 ----------
    matched_bad_cases: list[str] = field(default_factory=list)  # 命中的历史 bad-case id
    is_new_bug: bool = False                   # 是否是应写入 golden-set 的新 bug
    bug_summary: dict[str, Any] = field(default_factory=dict)   # 若是新 bug，结构化摘要


@dataclass
class TestReport:
    """报告生成 Agent 的产出：完整测试报告 + 全过程留痕。"""

    plugin: str
    requirement: TestRequirement
    cases: list[TestCase] = field(default_factory=list)
    raw_data: list[RawData] = field(default_factory=list)
    diagnosis: Diagnosis | None = None
    report_md: str = ""
    stage_errors: list[str] = field(default_factory=list)  # 降级时记录哪段出错
