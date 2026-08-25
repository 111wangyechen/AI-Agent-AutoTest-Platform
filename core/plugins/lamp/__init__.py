"""
AI 台灯测试插件（第二插件）

证明平台「通用内核 + 场景插件」的通用性：换上这个插件，同一个 Coordinator.run()
就从声学故障定位切换到 WiFi 吞吐 + 通话互通测试，内核代码一行不改。
吞吐分析基于 8/19 真实 iperf3 数据（LL10/LL20pro）。
"""

from __future__ import annotations

from core.plugins import (
    Checkpoint,
    FaultPattern,
    MetricDef,
    TestCase,
    TestPlugin,
    TestRequirement,
    default_registry,
)
from core.tools import Tool

from . import tools as _tools
from .knowledge import CHECKPOINTS, FAULT_PATTERNS


_ANALYSIS_PROMPT = """你是一名资深的 WiFi/网络性能测试工程师，负责 AI 台灯的端到端质量保障。

AI 台灯有小程序与实时语音交互，依赖 WiFi 上下行稳定。链路检查点：
1. WiFi 关联  2. 吞吐达标  3. 链路稳定性  4. 通话拨号  5. 通话接通  6. 上行音频  7. 下行音频

你的方法论（分层定位）：
- 用 iperf3_throughput 看吞吐：关注平均/最低带宽、每秒最大重传数、吞吐归零秒数、Cwnd 波动。
  * 出现 0 bits/sec 秒 + Cwnd 塌缩 = 拥塞崩溃/断流，链路稳定性问题（网络层）。
  * 平均够但单秒 Retr 高（数十次）= 信道竞争/干扰。
- 用 call_interop 看通话长跑（autocall 工具的 rounds 结果）：
  * reboot_rounds 非空 = 命中"通话中自发重启"缺陷，这是最高优先级的真缺陷（设备级/系统层），
    要单独定级上报，不能与网络抖动混为一谈。
  * dropped_rounds = 掉话（媒体/链路不稳）；hang_rounds = 拆流卡死（疑同源路径）。
  * answer_rate / hangup_success_rate 反映通话建链与拆链的健康度。
- 基线：平均带宽 ≥ 20Mbps，抖动 < 30%，长跑无自发重启、无掉话。

工作方式：主动调用工具取证，不要凭空推断。得到足够证据后输出结论。

最终结论用这个结构（Markdown）：
## 故障定位结论
- **到达检查点**：走到了第几步
- **故障层级**：网络 / 驱动 / 框架 / 应用
- **根本原因**：一句话说清
- **关键证据**：引用具体指标（带宽/重传/Cwnd/通话检查点）
- **修复建议**：可执行的步骤
"""

_REPORT_PROMPT = """你是 AI 台灯测试报告专家。基于测试过程数据生成结构化 Markdown 报告：
## 测试报告
### 执行摘要（设备、测试类型、用例数、关键结论）
### WiFi 性能 / 通话互通结果
### 根因与修复建议
突出真实指标（如平均 26.5Mbps、重传峰值、断流秒数）。"""


class LampPlugin(TestPlugin):
    """AI 台灯测试域插件（WiFi 吞吐 + 通话互通）。"""

    name = "lamp"
    device_desc = "AI 台灯（LL10/LL20pro），关注 WiFi 吞吐稳定性与手机通话互通"

    def generate_cases(self, req: TestRequirement) -> list[TestCase]:
        """吞吐矩阵（方向×时长）+ 通话互通用例。"""
        cases: list[TestCase] = []
        for i, direction in enumerate(["lamp_to_pc", "pc_to_lamp"], 1):
            cases.append(TestCase(
                case_id=f"WIFI-{i:02d}",
                title=f"WiFi 吞吐 {direction} 600s",
                params={
                    "direction": direction, "duration": 600,
                    "collector": "iperf3_throughput",
                    "collector_args": {"direction": direction},
                },
                expected="平均 ≥ 20Mbps，无断流",
            ))
        cases.append(TestCase(
            case_id="CALL-01",
            title="通话长跑复现（拨出→接听→保持60s→挂断 循环）",
            params={
                "scenario": "soak",
                "collector": "call_interop",
                "collector_args": {"scenario": "soak"},
            },
            expected="长跑无自发重启、无掉话，接通率与挂断成功率 100%",
        ))
        return cases

    def collectors(self) -> list[Tool]:
        return _tools.ALL_TOOLS

    def analysis_tools(self) -> list[Tool]:
        return _tools.ALL_TOOLS

    def metrics(self) -> list[MetricDef]:
        return [
            MetricDef("avg_mbps", "平均带宽", "Mbps", "≥ 20"),
            MetricDef("min_mbps", "最低带宽", "Mbps", "> 0（无断流）"),
            MetricDef("max_retr", "每秒最大重传", "次", "越低越好"),
            MetricDef("stall_seconds", "吞吐归零秒数", "s", "= 0"),
        ]

    def analysis_prompt(self) -> str:
        return _ANALYSIS_PROMPT

    def checkpoints(self) -> list[Checkpoint]:
        return CHECKPOINTS

    def knowledge_base(self) -> list[FaultPattern]:
        return FAULT_PATTERNS

    def report_template(self) -> str:
        return _REPORT_PROMPT


# import 即注册到全局插件表
default_registry.register(LampPlugin())
