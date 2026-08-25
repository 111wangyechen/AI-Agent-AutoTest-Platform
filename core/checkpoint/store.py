"""
CheckpointStore · checkpoint 的原子化存取

设计要点（对应面试稿承诺）：
- 存 workspace/.checkpoints/，按时间戳命名
- 原子写：先写 .tmp 再 os.replace，避免写一半崩溃留半成品
- 保留最近 N 个（默认 3），旧的自动清理 —— 支持「损坏时降级到上一个快照」
- latest.json 记录最新指针（Windows 无软链，用小文件存文件名）
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import TestCheckpoint


class CheckpointStore:
    """管理一个 workspace 下的所有 checkpoint。"""

    CKPT_DIR = ".checkpoints"
    LATEST_PTR = "latest.json"
    KEEP_RECENT = 3  # 保留最近 3 个，兜底损坏降级

    def __init__(self, workspace_path: str) -> None:
        self.workspace = Path(workspace_path)
        self.ckpt_dir = self.workspace / self.CKPT_DIR
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 保存 ----------

    def save(self, ckpt: TestCheckpoint) -> Path:
        """原子化保存一个 checkpoint，更新 latest 指针，清理过期。"""
        fname = f"{ckpt.checkpoint_id}.json"
        target = self.ckpt_dir / fname
        tmp = self.ckpt_dir / (fname + ".tmp")

        payload = json.dumps(ckpt.to_dict(), ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)  # 原子替换

        self._write_latest(fname)
        self._rotate()
        return target

    def _write_latest(self, fname: str) -> None:
        """更新 latest 指针（Windows 友好，用小 JSON 文件替代软链）。"""
        ptr = self.ckpt_dir / self.LATEST_PTR
        tmp = self.ckpt_dir / (self.LATEST_PTR + ".tmp")
        tmp.write_text(json.dumps({"latest": fname}), encoding="utf-8")
        os.replace(tmp, ptr)

    def _rotate(self) -> None:
        """只保留最近 KEEP_RECENT 个 checkpoint 文件，旧的删除。"""
        files = sorted(
            [p for p in self.ckpt_dir.glob("ckpt_*.json")],
            key=lambda p: p.name,
            reverse=True,
        )
        for stale in files[self.KEEP_RECENT:]:
            try:
                stale.unlink()
            except OSError:
                pass

    # ---------- 读取 ----------

    def load_latest(self) -> TestCheckpoint | None:
        """
        读最新 checkpoint；若最新文件损坏，自动降级到上一个（面试稿承诺的降级路径）。
        全部损坏或没有则返回 None。
        """
        # 先按 latest 指针取，失败再按文件名倒序逐个尝试
        candidates: list[str] = []
        ptr = self.ckpt_dir / self.LATEST_PTR
        if ptr.exists():
            try:
                latest_name = json.loads(ptr.read_text(encoding="utf-8")).get("latest")
                if latest_name:
                    candidates.append(latest_name)
            except (json.JSONDecodeError, OSError):
                pass

        # 补上所有 ckpt 文件（倒序），去重
        for p in sorted(self.ckpt_dir.glob("ckpt_*.json"), key=lambda p: p.name, reverse=True):
            if p.name not in candidates:
                candidates.append(p.name)

        for name in candidates:
            ckpt = self._try_load(self.ckpt_dir / name)
            if ckpt is not None:
                return ckpt
        return None

    def _try_load(self, path: Path) -> TestCheckpoint | None:
        """尝试加载单个 checkpoint 文件，损坏返回 None（触发降级）。"""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TestCheckpoint.from_dict(data)
        except (json.JSONDecodeError, OSError, TypeError, KeyError):
            return None

    def list_checkpoints(self) -> list[str]:
        """当前保留的 checkpoint 文件名（倒序），调试用。"""
        return [p.name for p in sorted(
            self.ckpt_dir.glob("ckpt_*.json"), key=lambda p: p.name, reverse=True
        )]
