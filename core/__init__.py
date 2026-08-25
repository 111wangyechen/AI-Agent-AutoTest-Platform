"""
AI Agent 测试平台核心包。

通用内核（与测试域无关）：
- config      运行配置（鉴权/设备/mock）
- tools       Tool 抽象 + ToolRegistry
- plugins     TestPlugin 契约 + 数据模型 + 注册表
- agents      LLMAgent 基类 + 5 子 Agent + Coordinator 主控

场景插件（core.plugins.acoustic / core.plugins.lamp）实现契约即接入。
"""

from .config import config

__all__ = ["config"]
