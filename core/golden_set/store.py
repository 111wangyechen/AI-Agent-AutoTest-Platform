"""
Golden-set · JSON 文件存取

每条 bad-case 一个 JSON 文件（`bad_cases/<case_id>.json`），git 可追踪。
写入用原子替换（先写 .tmp 再 os.replace），避免写一半崩溃留半成品。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .models import BadCase


class BadCaseStore:
    """一个目录下所有 bad-case 的 JSON 存取。"""

    CASES_DIR = "bad_cases"

    def __init__(self, root_path: str) -> None:
        self.root = Path(root_path)
        self.cases_dir = self.root / self.CASES_DIR
        self.cases_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 写 ----------

    def save(self, case: BadCase) -> Path:
        """原子化保存单条 bad-case。同名会覆盖（更新场景）。"""
        target = self.cases_dir / f"{case.case_id}.json"
        tmp = self.cases_dir / f"{case.case_id}.json.tmp"
        tmp.write_text(
            json.dumps(case.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, target)
        return target

    def bump_seen(self, case_id: str) -> BadCase | None:
        """再次观察到某 case 时，累加 seen_count 并更新 last_seen。"""
        case = self.load(case_id)
        if case is None:
            return None
        case.seen_count += 1
        case.last_seen = datetime.now().isoformat(timespec="seconds")
        self.save(case)
        return case

    # ---------- 读 ----------

    def load(self, case_id: str) -> BadCase | None:
        """按 ID 读取一条；不存在或损坏返回 None。"""
        p = self.cases_dir / f"{case_id}.json"
        if not p.exists():
            return None
        try:
            return BadCase.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError, KeyError):
            return None

    def load_all(self) -> list[BadCase]:
        """加载所有 bad-case，用于全量重建索引。"""
        cases: list[BadCase] = []
        for p in sorted(self.cases_dir.glob("bc_*.json")):
            try:
                cases.append(BadCase.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError, TypeError, KeyError):
                continue
        return cases

    def list_ids(self) -> list[str]:
        """当前所有 case_id。"""
        return [p.stem for p in sorted(self.cases_dir.glob("bc_*.json"))]

    # ---------- 删 ----------

    def delete(self, case_id: str) -> bool:
        """删除单条；不存在返回 False。删错了从 git 恢复即可。"""
        p = self.cases_dir / f"{case_id}.json"
        if not p.exists():
            return False
        p.unlink()
        return True
