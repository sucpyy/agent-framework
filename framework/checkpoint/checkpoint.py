"""Checkpoint v0.8 — 全量 Workspace 序列化 + 断点恢复。

生产级：完整保存 Workspace 状态，支持任意断点恢复。
"""

from __future__ import annotations

import gzip
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Checkpoint:
    """单个状态快照——包含完整执行上下文。"""
    checkpoint_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    task_id: str = ""
    subtask_states: dict[str, str] = field(default_factory=dict)
    workspace_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    version: str = "0.8"
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "subtask_states": self.subtask_states,
            "workspace_snapshot": self.workspace_snapshot,
            "created_at": self.created_at,
            "version": self.version,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Checkpoint":
        return cls(
            checkpoint_id=d["checkpoint_id"],
            task_id=d.get("task_id", ""),
            subtask_states=d.get("subtask_states", {}),
            workspace_snapshot=d.get("workspace_snapshot", {}),
            created_at=d.get("created_at", 0),
            version=d.get("version", "0.1"),
            data=d.get("data", {}),
        )


class CheckpointStore:
    """快照持久化存储——支持压缩、增量、自动清理。"""

    def __init__(
        self,
        base_dir: str = ".checkpoints",
        max_checkpoints: int = 50,
        compress: bool = True,
    ):
        self._base = Path(base_dir)
        self._max = max_checkpoints
        self._compress = compress
        self._checkpoints: dict[str, Checkpoint] = {}
        self._base.mkdir(parents=True, exist_ok=True)

    def save(self, checkpoint: Checkpoint) -> str:
        """保存快照到内存 + 磁盘。"""
        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        path = self._path_for(checkpoint.checkpoint_id)

        raw = json.dumps(checkpoint.to_dict(), indent=2, ensure_ascii=False)
        if self._compress:
            with gzip.open(str(path), "wt", encoding="utf-8") as f:
                f.write(raw)
        else:
            path.write_text(raw, encoding="utf-8")

        self._cleanup()
        return checkpoint.checkpoint_id

    def load(self, checkpoint_id: str) -> Checkpoint | None:
        """从磁盘加载快照。"""
        if checkpoint_id in self._checkpoints:
            return self._checkpoints[checkpoint_id]

        path = self._path_for(checkpoint_id)
        if not path.exists():
            return None

        try:
            if self._compress:
                with gzip.open(str(path), "rt", encoding="utf-8") as f:
                    data = json.loads(f.read())
            else:
                data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, gzip.BadGzipFile, EOFError):
            return None

        cp = Checkpoint.from_dict(data)
        self._checkpoints[checkpoint_id] = cp
        return cp

    def list_all(self) -> list[dict[str, Any]]:
        """列出所有快照摘要。"""
        summaries = []
        for path in sorted(self._base.glob("*.json*"), key=lambda p: p.stat().st_mtime, reverse=True):
            cid = path.stem.replace(".json", "")
            cp = self._checkpoints.get(cid)
            if cp:
                summaries.append({
                    "checkpoint_id": cid,
                    "task_id": cp.task_id,
                    "created_at": cp.created_at,
                    "version": cp.version,
                })
            else:
                summaries.append({
                    "checkpoint_id": cid,
                    "task_id": "unknown",
                    "created_at": path.stat().st_mtime,
                })
        return summaries

    def delete(self, checkpoint_id: str) -> None:
        self._checkpoints.pop(checkpoint_id, None)
        path = self._path_for(checkpoint_id)
        if path.exists():
            path.unlink()

    def _path_for(self, checkpoint_id: str) -> Path:
        ext = ".json.gz" if self._compress else ".json"
        return self._base / f"{checkpoint_id}{ext}"

    def _cleanup(self) -> None:
        """超出上限时删除旧快照。"""
        all_files = sorted(
            self._base.glob("*.json*"),
            key=lambda p: p.stat().st_mtime,
        )
        while len(all_files) > self._max:
            oldest = all_files.pop(0)
            cid = oldest.stem.replace(".json", "")
            self._checkpoints.pop(cid, None)
            oldest.unlink()

    def __len__(self) -> int:
        return len(self._checkpoints)
