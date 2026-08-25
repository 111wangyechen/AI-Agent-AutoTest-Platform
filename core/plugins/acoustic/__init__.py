"""
声学测试插件

平台的首个测试域，包含多个设备插件：
- lamp_plugin: AI 台灯（LL20）
- lingxi_plugin: 灵犀屏（待实现）
- unicom_plugin: 联通中屏（待实现）

所有设备插件继承自 AcousticPluginBase，共享分层定位方法论和故障知识库。
"""

# 导入 AI 台灯插件（会自动注册）
from . import lamp_plugin  # noqa: F401

# 旧的声学插件（临时保留，待完全迁移后删除）
# from . import acoustic_old  # noqa: F401
