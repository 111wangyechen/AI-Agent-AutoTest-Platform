"""
声学插件 · 知识：7 层检查点 + 故障模式库

这些是「资深工程师分层定位」方法论的结构化沉淀，供结果分析 Agent 研判。
"""

from __future__ import annotations

from core.plugins import Checkpoint, FaultPattern


# 语音链路的 7 个检查点（对应二面讲的链路）
CHECKPOINTS = [
    Checkpoint(1, "唤醒触发", "应用", ["KW_LOGIC"]),
    Checkpoint(2, "音频流开启", "框架", ["audio_hw_primary", "DeviceAccessService"]),
    Checkpoint(3, "3A 算法初始化", "中间件", ["audio_3a"]),
    Checkpoint(4, "音频数据采集", "框架", ["IFarVoiceDataReceiver"]),
    Checkpoint(5, "云端识别", "云端", ["LAST_IAT", "TimeRecorder"]),
    Checkpoint(6, "语义理解应答", "云端", ["NLUManager", "TTSpeakManager"]),
    Checkpoint(7, "资源释放", "框架", ["DeviceAccessService"]),
]


# 故障模式知识库（症状 → 根因 → 修复）
FAULT_PATTERNS = [
    FaultPattern(
        symptom="唤醒后部分无响应，日志有 open pcm failed",
        signature="audio_hw_primary: open pcm failed: Device or resource busy",
        layer="框架/应用",
        root_cause="上一次会话的 PCM 输入流未释放，连播时后一次唤醒抢占同一音频设备",
        fix="在会话结束路径补 adev_close_input_stream 的确定性调用（finally 兜底）",
    ),
    FaultPattern(
        symptom="上报唤醒率=0，但设备实际有反应",
        signature="serial_diagnose RX=0 / 收不到 RX 行",
        layer="硬件",
        root_cause="串口物理链路死，麦克风/采集硬件异常",
        fix="交硬件排查，绝不能按唤醒率=0 上报（那是软件指标口径错误）",
    ),
    FaultPattern(
        symptom="唤醒触发正常但识别无结果",
        signature="有 KW_LOGIC，无 LAST_IAT / 云端超时",
        layer="云端",
        root_cause="音频上传或云端识别链路故障（网络/服务端）",
        fix="查网络吞吐与云端服务状态，区分端侧采集与云端识别",
    ),
]
