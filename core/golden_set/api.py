"""
Golden-set · 对外统一 API

GoldenSet 把 JSON 存储（store）和 SQLite 索引（index）组装起来，对外提供：
- add(case)          新增/更新一条 bad-case（同时写 JSON + 索引）
- query(...)         按设备/tag/日志签名检索相似 case
- get(case_id)       取完整 case
- record_occurrence  再次观察到某 case：累加计数（用于统计复现频率）
- rebuild_index      从 JSON 全量重建索引（索引损坏或迁移时）

单一真相源是 JSON 文件；SQLite 只是可重建的加速索引。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import config

from .models import BadCase, BadCaseStatus
from .store import BadCaseStore
from .index import GoldenSetIndex


# 默认 golden-set 根目录（项目根下 golden_set/）。可用环境变量覆盖。
def _default_root() -> str:
    import os
    env = os.getenv("GOLDEN_SET_ROOT")
    if env:
        return env
    # 项目根：core/golden_set/api.py 往上三级
    return str(Path(__file__).resolve().parents[2] / "golden_set")


class GoldenSet:
    """bad-case 知识库的统一入口。"""

    def __init__(self, root_path: str | None = None) -> None:
        self.root = root_path or _default_root()
        self.store = BadCaseStore(self.root)
        self.index = GoldenSetIndex(self.root)
        # 首次初始化：若索引为空但 JSON 有数据，自动重建
        self._ensure_index()

    def _ensure_index(self) -> None:
        cases = self.store.load_all()
        # 空库直接返回；有 JSON 但索引可能落后 → 重建一次（成本低）
        if cases:
            self.index.rebuild(cases)

    # ---------- 写 ----------

    def add(self, case: BadCase) -> str:
        """新增或更新一条 bad-case（JSON + 索引同步）。返回 case_id。"""
        self.store.save(case)
        self.index.upsert(case)
        return case.case_id

    def record_occurrence(self, case_id: str) -> BadCase | None:
        """再次观察到某 case，累加计数并更新索引。"""
        case = self.store.bump_seen(case_id)
        if case is not None:
            self.index.upsert(case)
        return case

    def mark_status(self, case_id: str, status: BadCaseStatus,
                    fixed_version: str = "") -> BadCase | None:
        """更新 case 状态（如 confirmed / resolved / regressed）。"""
        case = self.store.load(case_id)
        if case is None:
            return None
        case.status = status
        if fixed_version:
            case.fixed_version = fixed_version
        self.add(case)
        return case

    def remove(self, case_id: str) -> bool:
        """删除一条 bad-case。"""
        self.index.delete(case_id)
        return self.store.delete(case_id)

    # ---------- 读 ----------

    def get(self, case_id: str) -> BadCase | None:
        """取完整 case 数据。"""
        return self.store.load(case_id)

    def query(
        self,
        device_model: str = "",
        plugin: str = "",
        tags: list[str] | None = None,
        log_signatures: list[str] | None = None,
        status: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        检索相似 bad-case，返回按相关度倒序的候选（含摘要字段）。

        典型用法（分析 Agent）：给症状相关的 tag + 日志里出现的关键字，
        查是否有历史 case 命中。命中则可直接引用现有根因/修复，不用重新分析。
        """
        return self.index.search(
            device_model=device_model, plugin=plugin,
            tags=tags, log_signatures=log_signatures,
            status=status, limit=limit,
        )

    def query_summary(self, **kwargs: Any) -> str:
        """query 的文本版：返回给 LLM 直接读的 Markdown 摘要。"""
        results = self.query(**kwargs)
        if not results:
            return "（Golden-set 中未找到相似的历史 bad-case）"
        lines = [f"找到 {len(results)} 条相似历史 bad-case（按相关度排序）：\n"]
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. [{r['case_id']}] {r['title']}\n"
                f"   状态: {r['status']} | 层级: {r['layer']} | "
                f"严重度: {r['severity']} | 已观察 {r['seen_count']} 次\n"
                f"   现象: {r['symptom']}\n"
                f"   根因: {r['root_cause']}\n"
            )
        return "\n".join(lines)

    def rebuild_index(self) -> int:
        """从 JSON 全量重建索引，返回重建的 case 数。"""
        cases = self.store.load_all()
        self.index.rebuild(cases)
        return len(cases)
