"""
通用内核 · 插件注册表

按 name 注册/取用插件，供 demo 和 Coordinator 选择测试域。
"""

from __future__ import annotations

from .base import TestPlugin


class PluginRegistry:
    """插件的集中登记处。"""

    def __init__(self) -> None:
        self._plugins: dict[str, TestPlugin] = {}

    def register(self, plugin: TestPlugin) -> None:
        """注册一个插件，用 plugin.name 作 key。"""
        if not plugin.name:
            raise ValueError("插件必须有 name")
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> TestPlugin:
        """取一个插件，不存在则报清晰的错误。"""
        if name not in self._plugins:
            avail = ", ".join(self._plugins) or "(空)"
            raise KeyError(f"未注册的插件: {name}；可用: {avail}")
        return self._plugins[name]

    def names(self) -> list[str]:
        """已注册的插件名。"""
        return list(self._plugins)


# 全局默认注册表；插件包在 import 时把自己注册进来
default_registry = PluginRegistry()
