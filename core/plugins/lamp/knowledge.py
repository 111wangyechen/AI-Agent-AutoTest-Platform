"""
台灯插件 · 知识：WiFi/通话检查点 + 故障模式库

AI 台灯有小程序与实时语音交互，端到端质量取决于 WiFi 上下行稳定性与通话链路。
"""

from __future__ import annotations

from core.plugins import Checkpoint, FaultPattern


# WiFi 吞吐 + 通话互通的检查点
CHECKPOINTS = [
    Checkpoint(1, "WiFi 关联", "网络", ["wpa_supplicant", "dhcp"]),
    Checkpoint(2, "吞吐达标", "网络", ["iperf3", "bandwidth"]),
    Checkpoint(3, "链路稳定性", "网络", ["retr", "cwnd"]),
    Checkpoint(4, "通话拨号", "应用", ["dial"]),
    Checkpoint(5, "通话接通", "应用", ["connect"]),
    Checkpoint(6, "上行音频", "框架", ["uplink"]),
    Checkpoint(7, "下行音频", "框架", ["downlink"]),
]


FAULT_PATTERNS = [
    FaultPattern(
        symptom="吞吐周期性归零、语音卡顿",
        signature="iperf3 出现 0.00 bits/sec 秒 + Cwnd 塌缩到 1~2KB",
        layer="网络",
        root_cause="拥塞崩溃/信道抢占，TCP 拥塞窗口反复归零重建",
        fix="换信道/避开 2.4G 干扰，检查 AP 负载；台灯侧加弱网重连与音频缓冲",
    ),
    FaultPattern(
        symptom="平均带宽够但重传峰值高",
        signature="单秒 Retr 达数十次（如 88），带宽抖动大",
        layer="网络",
        root_cause="无线信道竞争或多设备争抢，链路质量不稳",
        fix="定位干扰源，绑定 5G 频段；基线要求平均≥20Mbps 且抖动<30%",
    ),
    FaultPattern(
        symptom="音视频通话中设备偶发自发重启",
        signature="autocall rounds CSV 出现 reboot=1（uptime 归零或 vchat PID 变，双通道确认）",
        layer="系统/设备",
        root_cause="cmcc_vchat 通话态资源路径异常触发整机复位（疑与拆流遇网络抖动 hang 同源）",
        fix="抓 reboot 现场（dmesg/串口 U-Boot 横幅/vchat 线程栈），定级为设备级缺陷单独上报，勿混入网络抖动",
    ),
    FaultPattern(
        symptom="通话保持中掉话",
        signature="autocall note 含'通话意外中断'，talk_sec 远小于设定 60s",
        layer="框架/网络",
        root_cause="媒体链路不稳或 vchat 会话中断（结合 iperf3 断流秒数交叉判断）",
        fix="对齐掉话时刻与吞吐归零秒；稳网络后重跑长跑确认掉话清零",
    ),
]
