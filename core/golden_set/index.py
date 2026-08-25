"""
Golden-set · SQLite 索引 + 检索

只索引"检索需要的字段"（设备、插件、tag、日志签名），完整 case 数据在 JSON 里。
索引损坏或缺失时可从 JSON 文件全量重建（rebuild）——JSON 是唯一真相源。

检索策略：
1. 精确过滤（device_model / plugin / status）—— 缩小候选范围
2. tag / log_signature 关键字命中打分 —— 命中越多得分越高
3. 返回按分数倒序的候选列表，供 Agent 二次判断

用 Python 内置 sqlite3，零外部依赖。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .models import BadCase


class GoldenSetIndex:
    """bad-case 的 SQLite 索引，与 JSON 存储一一对应。"""

    DB_NAME = "index.db"

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS bad_cases (
        case_id       TEXT PRIMARY KEY,
        title         TEXT NOT NULL,
        device_model  TEXT,
        plugin        TEXT,
        status        TEXT,
        severity      TEXT,
        symptom       TEXT,
        root_cause    TEXT,
        layer         TEXT,
        first_seen    TEXT,
        last_seen     TEXT,
        seen_count    INTEGER
    );

    CREATE INDEX IF NOT EXISTS idx_device ON bad_cases(device_model);
    CREATE INDEX IF NOT EXISTS idx_plugin ON bad_cases(plugin);
    CREATE INDEX IF NOT EXISTS idx_status ON bad_cases(status);

    CREATE TABLE IF NOT EXISTS tags (
        case_id  TEXT NOT NULL,
        tag      TEXT NOT NULL,
        PRIMARY KEY (case_id, tag)
    );
    CREATE INDEX IF NOT EXISTS idx_tag_tag ON tags(tag);

    CREATE TABLE IF NOT EXISTS log_signatures (
        case_id    TEXT NOT NULL,
        signature  TEXT NOT NULL,
        PRIMARY KEY (case_id, signature)
    );
    CREATE INDEX IF NOT EXISTS idx_sig_sig ON log_signatures(signature);
    """

    def __init__(self, root_path: str) -> None:
        self.root = Path(root_path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / self.DB_NAME
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(self.SCHEMA)

    def _conn(self):
        """
        返回一个 with 语句结束时会自动 commit + close 的连接管理器。

        `with sqlite3.Connection` 原生只 commit/rollback，不 close；Windows 上不 close
        会锁住 db 文件、导致 temp 目录清理失败与单测不稳。这里手写一个包装：
        正常退出 commit，异常退出 rollback，无论哪种都 close。
        """
        from contextlib import contextmanager

        @contextmanager
        def _managed():
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        return _managed()

    # ---------- 写 ----------

    def upsert(self, case: BadCase) -> None:
        """插入或更新一条 case 的索引。"""
        with self._conn() as c:
            c.execute("""
                INSERT INTO bad_cases (case_id, title, device_model, plugin, status,
                                       severity, symptom, root_cause, layer,
                                       first_seen, last_seen, seen_count)
                VALUES (:case_id, :title, :device_model, :plugin, :status,
                        :severity, :symptom, :root_cause, :layer,
                        :first_seen, :last_seen, :seen_count)
                ON CONFLICT(case_id) DO UPDATE SET
                    title=excluded.title,
                    device_model=excluded.device_model,
                    plugin=excluded.plugin,
                    status=excluded.status,
                    severity=excluded.severity,
                    symptom=excluded.symptom,
                    root_cause=excluded.root_cause,
                    layer=excluded.layer,
                    last_seen=excluded.last_seen,
                    seen_count=excluded.seen_count
            """, {
                "case_id": case.case_id, "title": case.title,
                "device_model": case.device_model, "plugin": case.plugin,
                "status": case.status.value, "severity": case.severity,
                "symptom": case.symptom, "root_cause": case.root_cause,
                "layer": case.layer, "first_seen": case.first_seen,
                "last_seen": case.last_seen, "seen_count": case.seen_count,
            })
            # 重建 tag / signature（先删后插，简单可靠）
            c.execute("DELETE FROM tags WHERE case_id=?", (case.case_id,))
            for tag in case.tags:
                c.execute("INSERT OR IGNORE INTO tags VALUES (?, ?)", (case.case_id, tag))
            c.execute("DELETE FROM log_signatures WHERE case_id=?", (case.case_id,))
            for sig in case.log_signatures:
                c.execute("INSERT OR IGNORE INTO log_signatures VALUES (?, ?)",
                          (case.case_id, sig))

    def delete(self, case_id: str) -> None:
        """从索引删除某 case。"""
        with self._conn() as c:
            c.execute("DELETE FROM bad_cases WHERE case_id=?", (case_id,))
            c.execute("DELETE FROM tags WHERE case_id=?", (case_id,))
            c.execute("DELETE FROM log_signatures WHERE case_id=?", (case_id,))

    def rebuild(self, cases: list[BadCase]) -> None:
        """从 JSON 全量重建索引（索引损坏或首次初始化时用）。"""
        # 确保 schema 存在（rebuild 可能被首次初始化调用，此时表还不存在）
        self._init_schema()
        with self._conn() as c:
            c.executescript("""
                DELETE FROM bad_cases;
                DELETE FROM tags;
                DELETE FROM log_signatures;
            """)
        for case in cases:
            self.upsert(case)

    # ---------- 检索 ----------

    def search(
        self,
        device_model: str = "",
        plugin: str = "",
        tags: list[str] | None = None,
        log_signatures: list[str] | None = None,
        status: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        按条件检索 bad-case，返回按分数倒序的候选 [(case_id, score, meta)...]。

        评分：tag 命中 +2 分/个，log_signature 命中 +3 分/个（日志更精确）。
        device/plugin/status 用作精确过滤（不进评分）。
        """
        tags = tags or []
        log_signatures = log_signatures or []

        # 收集所有初步命中的 case_id + 分数
        scores: dict[str, int] = {}

        with self._conn() as c:
            if tags:
                placeholders = ",".join("?" * len(tags))
                rows = c.execute(
                    f"SELECT case_id, COUNT(*) as hits FROM tags "
                    f"WHERE tag IN ({placeholders}) GROUP BY case_id",
                    tags,
                ).fetchall()
                for r in rows:
                    scores[r["case_id"]] = scores.get(r["case_id"], 0) + r["hits"] * 2

            if log_signatures:
                # log_signature 用 LIKE 支持子串匹配（更宽容）
                for sig in log_signatures:
                    rows = c.execute(
                        "SELECT DISTINCT case_id FROM log_signatures "
                        "WHERE signature LIKE ?",
                        (f"%{sig}%",),
                    ).fetchall()
                    for r in rows:
                        scores[r["case_id"]] = scores.get(r["case_id"], 0) + 3

            # 没提供任何匹配条件 → 按精确过滤返回全部（用于列出所有 case）
            if not tags and not log_signatures:
                sql = "SELECT case_id FROM bad_cases WHERE 1=1"
                args: list[Any] = []
                if device_model:
                    sql += " AND device_model=?"
                    args.append(device_model)
                if plugin:
                    sql += " AND plugin=?"
                    args.append(plugin)
                if status:
                    sql += " AND status=?"
                    args.append(status)
                for r in c.execute(sql, args).fetchall():
                    scores[r["case_id"]] = 1  # 默认分

            if not scores:
                return []

            # 按精确过滤过滤 case_id
            case_ids = list(scores.keys())
            placeholders = ",".join("?" * len(case_ids))
            sql = f"SELECT * FROM bad_cases WHERE case_id IN ({placeholders})"
            args = list(case_ids)
            if device_model:
                sql += " AND device_model=?"
                args.append(device_model)
            if plugin:
                sql += " AND plugin=?"
                args.append(plugin)
            if status:
                sql += " AND status=?"
                args.append(status)
            rows = c.execute(sql, args).fetchall()

        results = [
            {
                "case_id": r["case_id"],
                "score": scores[r["case_id"]],
                "title": r["title"],
                "device_model": r["device_model"],
                "plugin": r["plugin"],
                "status": r["status"],
                "severity": r["severity"],
                "symptom": r["symptom"],
                "root_cause": r["root_cause"],
                "layer": r["layer"],
                "seen_count": r["seen_count"],
            }
            for r in rows
        ]
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]
