"""
通用内核 · Golden-set bad-case 知识库

沉淀真实测试遇到的 bug，让下次遇到类似问题时 Agent 能快速匹配、直接引用现有结论，
避免重复分析。设计原则：
- 每条 bad-case 一个 JSON 文件（人可读、git 可追踪、方便代码评审）
- SQLite 做索引（按 tag/设备/关键字/日志签名快速检索）
- 检索接口对 Agent 友好：一次 query 返回相似度排序的候选列表

模块划分：
- models.py    BadCase 数据模型
- store.py     JSON 文件存取
- index.py     SQLite 索引 + 检索
- api.py       对外统一 API（GoldenSet.query / add / remove）
- tools.py     Agent 工具封装（query_golden_set）
"""

from __future__ import annotations

from .models import BadCase, BadCaseStatus
from .store import BadCaseStore
from .index import GoldenSetIndex
from .api import GoldenSet
from .tools import QUERY_GOLDEN_SET_TOOL

__all__ = [
    "BadCase",
    "BadCaseStatus",
    "BadCaseStore",
    "GoldenSetIndex",
    "GoldenSet",
    "QUERY_GOLDEN_SET_TOOL",
]
