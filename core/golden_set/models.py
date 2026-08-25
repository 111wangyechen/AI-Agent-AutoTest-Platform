"""
Golden-set · 数据模型

BadCase：一条真实测试遇到的 bug 记录。核心字段设计原则：
- 现象描述用自然语言（Agent 友好）
- 日志签名用关键字数组（SQLite 索引精确匹配）
- 根因/修复用结构化（便于回归检查是否已解决）
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


class BadCaseStatus(str, Enum):
    """bad-case 状态。"""

    HYPOTHESIS = "hypothesis"  # 假设（仅一次观察，未确认）
    CONFIRMED = "confirmed"    # 已确认（多次复现或研发确认）
    RESOLVED = "resolved"      # 已修复（有对应版本号）
    REGRESSED = "regressed"    # 曾经修复现在又复现（重要！）


@dataclass
class BadCase:
    """
    一条 bad-case 记录：症状 + 日志签名 + 根因 + 修复 + 证据链。

    检索时的关键字段：
    - device_model / plugin: 精确过滤（不同设备的 bug 不通用）
    - tags / log_signatures: 关键字匹配（Agent 用症状描述查这些）
    - symptom: LLM 二次筛选（tag 命中后用症状描述精确判断）
    """

    # 主键
    case_id: str                          # 如 bc_20260825_六次唤醒后闲聊吞唤醒
    title: str                            # 一句话概括

    # 分类
    device_model: str = ""                # 如 AI台灯·LL20
    plugin: str = ""                      # 如 acoustic_lamp
    status: BadCaseStatus = BadCaseStatus.HYPOTHESIS
    severity: str = "medium"              # low / medium / high / critical
    tags: list[str] = field(default_factory=list)  # 如 ["唤醒", "不应期", "闲聊模式"]

    # 现象 & 签名（检索核心）
    symptom: str = ""                     # 自然语言现象描述（供 LLM 二次匹配）
    log_signatures: list[str] = field(default_factory=list)
    # 日志关键字签名，如 ["LISTEN_MSG_VAD_END", "DeviceAccessService: give up"]
    # 检索时用 grep 一样精确匹配，命中一个就算候选

    # 复现条件
    reproduce_steps: list[str] = field(default_factory=list)
    reproduce_rate: str = ""              # 如 "100%" / "6/300"

    # 根因 & 修复
    root_cause: str = ""                  # 一段话说清
    layer: str = ""                       # 硬件 / 驱动 / 框架 / 应用 / 云端
    fix_suggestion: str = ""              # 建议的修复方案
    fixed_version: str = ""               # 修复的版本号（若已修）

    # 证据链
    evidence_paths: list[str] = field(default_factory=list)
    # 相关日志/音频/报告文件的路径，方便研发回溯

    # 元数据
    first_seen: str = ""                  # ISO 时间
    last_seen: str = ""
    seen_count: int = 1                   # 累计观察次数
    related_case_ids: list[str] = field(default_factory=list)  # 关联 case

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BadCase":
        d = dict(d)
        status_val = d.pop("status", BadCaseStatus.HYPOTHESIS.value)
        try:
            status = BadCaseStatus(status_val)
        except ValueError:
            status = BadCaseStatus.HYPOTHESIS
        known = {f for f in cls.__dataclass_fields__}  # noqa
        obj = cls(**{k: v for k, v in d.items() if k in known})
        obj.status = status
        return obj

    @classmethod
    def new(
        cls,
        title: str,
        device_model: str = "",
        plugin: str = "",
        **kwargs: Any,
    ) -> "BadCase":
        """便捷构造：自动生成 case_id 和时间戳。"""
        now = datetime.now()
        # case_id 用时间戳 + 标题的前 20 字符（去空格），便于人肉识别
        clean_title = title.replace(" ", "").replace("/", "_")[:20]
        case_id = f"bc_{now.strftime('%Y%m%d_%H%M%S')}_{clean_title}"
        iso = now.isoformat(timespec="seconds")
        return cls(
            case_id=case_id,
            title=title,
            device_model=device_model,
            plugin=plugin,
            first_seen=iso,
            last_seen=iso,
            **kwargs,
        )
