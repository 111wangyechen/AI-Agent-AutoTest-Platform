"""
平台配置

集中管理运行参数，支持环境变量覆盖，方便在实验室现场调整。
"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """运行配置"""

    # Claude API
    # 默认用最强的 Opus 4.8；成本敏感时可设 ANTHROPIC_MODEL=claude-haiku-4-5
    model: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
    # 两种鉴权：官方直连用 api_key；公司网关(讯飞 one.iflytek.com)用 auth_token + base_url
    api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    auth_token: str = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
    base_url: str = os.getenv("ANTHROPIC_BASE_URL", "")

    # 设备连接（无线 adb 为主）
    # 现场把灵犀屏的 IP 填到 DEVICE_ADDR，例如 192.168.211.5:5555
    device_addr: str = os.getenv("DEVICE_ADDR", "")

    # 运行模式
    # mock=True 时用内置样本日志，不连设备、不跑 adb（在家先验证分析质量）
    mock: bool = os.getenv("PLATFORM_MOCK", "0") == "1"

    # logcat 抓取的最大行数（避免一次拉太多）
    logcat_lines: int = int(os.getenv("LOGCAT_LINES", "800"))

    # 台灯插件：真机跑 iperf3 时打到的 server 地址（电脑侧 iperf3 -s 的 IP）
    iperf3_server: str = os.getenv("IPERF3_SERVER", "192.168.211.2")

    @property
    def has_device(self) -> bool:
        return bool(self.device_addr) and not self.mock

    @property
    def has_credentials(self) -> bool:
        """官方 api_key 或公司网关 auth_token，任一存在即可调用。"""
        return bool(self.api_key or self.auth_token)


# 全局单例
config = Config()
