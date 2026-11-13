"""EventLog v0.8 — 增量追加 + 自动压缩。

持久化行为历史，支持按事件类型查询、回放、自动清理。
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from typing import Any


class EventLog:
    """持久化事件日志——增量追加、可查询、可重放。"""

    def __init__(
        self,
        file_path: str | None = None,
        max_entries: int = 10000,
        auto_save: bool = False,
    ):
        self._entries: list[dict[str, Any]] = []
        self._file_path = Path(file_path) if file_path else None
        self._max = max_entries
        self._auto_save = auto_save
        self._dirty = False

    def append(self, event: str, payload: dict[str, Any] | None = None) -> None:
        entry = {
            "timestamp": time.time(),
            "event": event,
            "payload": payload or {},
        }
        self._entries.append(entry)
        self._dirty = True
        self._trim()

        if self._auto_save:
            self.save()

    def query(
        self,
        event_filter: str | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """查询事件——支持按类型、时间范围过滤。"""
        results = self._entries

        if event_filter:
            results = [e for e in results if e["event"] == event_filter]

        if since is not None:
            results = [e for e in results if e["timestamp"] >= since]

        if limit is not None:
            results = results[-limit:]

        return results

    def replay(self) -> list[dict[str, Any]]:
        """按时间顺序重放全部事件。"""
        return sorted(self._entries, key=lambda e: e["timestamp"])

    def count_by_event(self) -> dict[str, int]:
        """按事件类型统计。"""
        counts: dict[str, int] = {}
        for e in self._entries:
            evt = e["event"]
            counts[evt] = counts.get(evt, 0) + 1
        return counts

    def save(self) -> None:
        """持久化到文件（支持压缩）。"""
        if not self._file_path:
            return
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

        if self._file_path.suffix == ".gz":
            with gzip.open(str(self._file_path), "wt", encoding="utf-8") as f:
                json.dump(self._entries, f, ensure_ascii=False)
        else:
            self._file_path.write_text(
                json.dumps(self._entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        self._dirty = False

    def load(self) -> None:
        """从文件加载。"""
        if not self._file_path or not self._file_path.exists():
            return

        try:
            if self._file_path.suffix == ".gz":
                with gzip.open(str(self._file_path), "rt", encoding="utf-8") as f:
                    self._entries = json.load(f)
            else:
                self._entries = json.loads(
                    self._file_path.read_text(encoding="utf-8")
                )
        except (json.JSONDecodeError, gzip.BadGzipFile, EOFError):
            self._entries = []

        self._trim()

    def clear(self) -> None:
        self._entries.clear()
        self._dirty = True

    def _trim(self) -> None:
        """超出上限时删除旧条目。"""
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]
            self._dirty = True

    def __len__(self) -> int:
        return len(self._entries)
