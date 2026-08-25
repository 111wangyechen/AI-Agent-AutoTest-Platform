"""
声学测试插件通用基类

设计理念：
- 声学测试的分层定位方法论、7层检查点、故障知识库是通用的（跨设备）
- 但不同设备的日志采集、音频采集、执行策略差异很大
- 用继承 + 抽象方法让子类只需实现设备特定部分

三款设备的差异：
1. 灵犀屏：实时 logcat + 八路音频自动存储（需轮询读取）
2. AI 台灯：全量日志自动存储到 /tmp/log.txt + 四路音频实时抓取（独占 adb）
3. 联通中屏：（待补充）
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Literal

from core.plugins import (
    Checkpoint,
    FaultPattern,
    MetricDef,
    TestCase,
    TestPlugin,
    TestRequirement,
)
from core.tools import Tool


# ========== 通用资源（跨设备共享） ==========

# 分层定位方法论 - 7 层检查点通用
SHARED_ANALYSIS_PROMPT = """你是一名资深的智能语音设备测试工程师，擅长对声学链路做分层故障定位。

被测设备是智能语音终端（灵犀屏/AI台灯/联通中屏等）。语音链路有 7 个检查点，按顺序：
1. 唤醒触发      tag: KW_LOGIC
2. 音频流开启    tag: audio_hw_primary, DeviceAccessService
3. 3A 算法初始化 tag: audio_3a
4. 音频数据采集  tag: IFarVoiceDataReceiver
5. 云端识别      tag: LAST_IAT, TimeRecorder
6. 语义理解应答  tag: NLUManager, TTSpeakManager
7. 资源释放      tag: DeviceAccessService

你的排查方法论（分层定位）：
- 先用日志采集工具按检查点 tag 过滤，判断执行走到了哪一层、在哪层报错。
- 关键区分：设备"真没醒"还是"链路故障"。二者在应用日志里都可能表现为无响应，
  必须用 serial_diagnose 看串口 RX 字节数 N —— N>0 链路活（问题在驱动/应用），
  N=0 或收不到 RX 行则物理链路死，绝不能上报为唤醒率=0，要交硬件。
- 需要确认进程/属性时用 adb_tool。
- 分析音频采集器产出的音频数据（如有）来判断声学质量。

工作方式：主动调用工具收集证据，不要凭空推断。得到足够证据后，输出结论。

最终结论用这个结构（Markdown）：
## 故障定位结论
- **到达检查点**：走到了第几步
- **故障层级**：硬件 / 驱动 / 框架 / 应用 / 云端
- **根本原因**：一句话说清
- **关键证据**：引用你抓到的具体日志行或串口判据
- **修复建议**：可执行的步骤
"""

SHARED_REPORT_PROMPT = """你是声学测试报告专家。基于给定的测试过程数据，生成结构化 Markdown 报告：
## 测试报告
### 执行摘要（设备、测试类型、用例数、关键结论）
### 测试结果（唤醒成功率、时序数据）
### 分层诊断结果（引用结果分析 Agent 的结论）
### 修复建议
语气专业、简洁，突出根因与证据。"""


# ========== 通用基类 ==========

ExecutionStrategy = Literal["parallel", "sequential_audio_first", "sequential_log_first"]


class AcousticPluginBase(TestPlugin):
    """
    声学测试通用基类

    子类需要实现的设备特定方法：
    - audio_playback_tool()   音频播放工具
    - log_collector()         日志采集器
    - audio_collector()       音频采集器（可选）
    - execution_strategy()    执行策略
    """

    # 设备标识（子类必须设置）
    device_model: str = ""  # "AI台灯" / "灵犀屏" / "联通中屏"

    # ========== 通用能力（子类继承） ==========

    def analysis_prompt(self) -> str:
        """分层定位方法论，所有声学设备通用"""
        return SHARED_ANALYSIS_PROMPT

    def report_template(self) -> str:
        """报告模板，所有声学设备通用"""
        return SHARED_REPORT_PROMPT

    def metrics(self) -> list[MetricDef]:
        """通用指标"""
        return [
            MetricDef("wake_rate", "唤醒率", "%", "≥ 90%"),
            MetricDef("pcm_fail", "音频流打开失败次数", "次", "= 0"),
            MetricDef("rx_bytes", "串口 RX 字节数", "byte", "> 0 表示链路活"),
        ]

    # ========== 设备特定能力（子类必须实现） ==========

    @abstractmethod
    def audio_playback_tool(self) -> Tool:
        """
        音频播放工具（设备特定）

        功能：推送音频文件到设备 + 播放
        输入参数：audio_file (本地路径)
        """
        pass

    @abstractmethod
    def log_collector(self) -> Tool:
        """
        日志采集器（设备特定）

        - 灵犀屏：实时 logcat dump
        - AI 台灯：读取 /tmp/log.txt 存储文件
        - 联通中屏：（待定）
        """
        pass

    @abstractmethod
    def audio_collector(self) -> Tool | None:
        """
        音频采集器（设备特定，可选）

        - 灵犀屏：从设备存储读取八路音频文件
        - AI 台灯：实时抓取四路音频（独占 adb）
        - 联通中屏：（待定）

        返回 None 表示该设备不需要采集音频
        """
        pass

    @abstractmethod
    def execution_strategy(self) -> ExecutionStrategy:
        """
        执行策略（设备特定）

        - "parallel": 播放音频的同时可以并行采集日志/音频（灵犀屏）
        - "sequential_audio_first": 先抓音频（独占 adb），完成后再读日志（AI 台灯）
        - "sequential_log_first": 先读日志，再抓音频（如果有设备需要）
        """
        pass

    # ========== 插件契约实现（基类组装） ==========

    def collectors(self) -> list[Tool]:
        """
        执行阶段采集器列表

        基类自动组装：播放工具 + 日志采集器 + 音频采集器（如有）
        """
        tools = [self.audio_playback_tool(), self.log_collector()]
        audio_tool = self.audio_collector()
        if audio_tool:
            tools.append(audio_tool)
        return tools

    def analysis_tools(self) -> list[Tool]:
        """
        分析阶段工具列表

        基类提供通用分析工具（adb、logcat、串口诊断）+ Golden-set 查询。
        子类可以扩展（如添加设备特定的诊断工具，见 lamp_plugin）。
        """
        from core.golden_set import QUERY_GOLDEN_SET_TOOL
        from .shared_tools import ALL_TOOLS

        return ALL_TOOLS + [QUERY_GOLDEN_SET_TOOL]
        from .shared_tools import ADB_TOOL, LOGCAT_TOOL, SERIAL_TOOL
        return [ADB_TOOL, LOGCAT_TOOL, SERIAL_TOOL]

    def checkpoints(self) -> list[Checkpoint]:
        """7 层检查点，所有声学设备通用"""
        from .shared_knowledge import CHECKPOINTS
        return CHECKPOINTS

    def knowledge_base(self) -> list[FaultPattern]:
        """故障模式知识库，所有声学设备通用"""
        from .shared_knowledge import FAULT_PATTERNS
        return FAULT_PATTERNS

    def generate_cases(self, req: TestRequirement) -> list[TestCase]:
        """
        生成测试用例（基类提供默认实现）

        子类可以重写以提供设备特定的用例矩阵
        """
        # 检查需求中是否指定了音频文件
        if "音频" in req.raw or "audio" in req.raw.lower():
            # 真实唤醒测试用例
            return [TestCase(
                case_id=f"{self.device_model}-REAL-01",
                title=f"{self.device_model}真实唤醒测试",
                params={
                    "collector": self.audio_playback_tool().name,
                    "collector_args": {
                        "audio_file": self._extract_audio_path(req.raw),
                        "expected_count": self._extract_expected_count(req.raw),
                    },
                },
                expected="唤醒率 ≥ 90%",
            )]
        else:
            # 默认用例：简单的日志采集
            return [TestCase(
                case_id=f"{self.device_model}-LOG-01",
                title=f"{self.device_model}日志采集",
                params={
                    "collector": self.log_collector().name,
                    "collector_args": {},
                },
                expected="无故障日志",
            )]

    def _extract_audio_path(self, raw: str) -> str:
        """从需求中提取音频路径（简单实现，子类可重写）"""
        # 默认返回空，让用例参数中明确指定
        import re
        match = re.search(r'[A-Z]:\\[^\s]+\.mp3', raw)
        return match.group(0) if match else ""

    def _extract_expected_count(self, raw: str) -> int:
        """从需求中提取预期次数（简单实现）"""
        import re
        match = re.search(r'(\d+)\s*次', raw)
        return int(match.group(1)) if match else 100
