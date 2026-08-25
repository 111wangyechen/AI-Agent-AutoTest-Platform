"""
通用内核 · 插件契约

TestPlugin 定义了「一个测试域」需要向平台提供的全部能力，恰好对应 5 个扩展点：
  用例模板  -> generate_cases()
  采集器    -> collectors()
  指标      -> metrics()
  研判规则  -> analysis_prompt() + checkpoints() + knowledge_base() + analysis_tools()
  报告      -> report_template()

内核（Coordinator + 5 子 Agent）只依赖这个抽象，不认识声学/台灯任何细节。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.tools import Tool
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


class TestPlugin(ABC):
    """一个测试域插件。子类实现 5 扩展点即可接入平台。"""

    #: 插件唯一标识（demo/registry 用它选插件）
    name: str = ""
    #: 被测设备/场景描述，拼进各 Agent 的 system prompt
    device_desc: str = ""

    # ---------- 扩展点 1：用例模板 ----------
    @abstractmethod
    def generate_cases(self, req: TestRequirement) -> list[TestCase]:
        """根据结构化需求生成测试用例矩阵。"""

    # ---------- 扩展点 2：采集器 ----------
    @abstractmethod
    def collectors(self) -> list[Tool]:
        """执行阶段用来采集原始数据的工具集（确定性调用，不经 LLM）。"""

    # ---------- 扩展点 3：指标 ----------
    @abstractmethod
    def metrics(self) -> list[MetricDef]:
        """本测试域关心的指标与判据。"""

    # ---------- 扩展点 4：研判规则（核心） ----------
    @abstractmethod
    def analysis_prompt(self) -> str:
        """结果分析 Agent 的 system prompt（分层定位方法论）。"""

    @abstractmethod
    def analysis_tools(self) -> list[Tool]:
        """结果分析 Agent 可主动调用的取证工具集。"""

    @abstractmethod
    def checkpoints(self) -> list[Checkpoint]:
        """链路检查点定义。"""

    @abstractmethod
    def knowledge_base(self) -> list[FaultPattern]:
        """故障模式知识库。"""

    # ---------- 扩展点 5：报告 ----------
    @abstractmethod
    def report_template(self) -> str:
        """报告生成 Agent 的 system prompt / 结构模板。"""

    # ---------- 可选：给需求分析 Agent 的提示 ----------
    def requirement_prompt(self) -> str:
        """需求分析 Agent 的 system prompt，默认给通用版本。"""
        return (
            f"你是测试需求分析专家。被测对象：{self.device_desc}。"
            "把用户的自然语言需求解析为结构化字段：设备、测试类型、关键指标、验收标准。"
        )
