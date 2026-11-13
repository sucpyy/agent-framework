"""Memory v1.0 — 分层记忆 + 长期文件存储。

短期窗口: deque（最近 N 轮上下文）
长期记忆: JSON 文件存储（第二阶段再换向量数据库）
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any


class Memory:
    """分层记忆——短期滑动窗口 + 长期文件存储。"""

    def __init__(
        self,
        window_size: int = 10,
        long_term_path: str | None = None,
    ):
        self._short_term: deque[dict[str, Any]] = deque(maxlen=window_size)
        self._long_term: dict[str, Any] = {}
        self._lt_path = Path(long_term_path) if long_term_path else None

        if self._lt_path and self._lt_path.exists():
            self._load_long_term()

    # ── Short-term ────────────────────────────

    @property
    def window_size(self) -> int:
        return self._short_term.maxlen  # type: ignore[return-value]

    def add(self, entry: dict[str, Any]) -> None:
        entry["_stored_at"] = time.time()
        self._short_term.append(entry)

    def recall(self, n: int | None = None) -> list[dict[str, Any]]:
        items = list(self._short_term)
        if n is not None:
            items = items[-n:]
        return items

    def search_short_term(self, keyword: str) -> list[dict[str, Any]]:
        """简单关键词搜索短期记忆。"""
        results = []
        for entry in self._short_term:
            entry_str = json.dumps(entry, ensure_ascii=False).lower()
            if keyword.lower() in entry_str:
                results.append(entry)
        return results

    # ── Long-term ─────────────────────────────

    def set_long_term(self, key: str, value: Any) -> None:
        self._long_term[key] = {
            "value": value,
            "stored_at": time.time(),
        }
        self._save_long_term()

    def get_long_term(self, key: str) -> Any | None:
        entry = self._long_term.get(key)
        return entry["value"] if entry else None

    def search_long_term(self, keyword: str) -> dict[str, Any]:
        """简单关键词搜索长期记忆。"""
        results = {}
        for key, entry in self._long_term.items():
            if (
                keyword.lower() in key.lower()
                or keyword.lower() in str(entry.get("value", "")).lower()
            ):
                results[key] = entry["value"]
        return results

    def _save_long_term(self) -> None:
        if not self._lt_path:
            return
        self._lt_path.parent.mkdir(parents=True, exist_ok=True)
        self._lt_path.write_text(
            json.dumps(self._long_term, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_long_term(self) -> None:
        if not self._lt_path or not self._lt_path.exists():
            return
        try:
            self._long_term = json.loads(
                self._lt_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, FileNotFoundError):
            self._long_term = {}

    # ── Utilities ─────────────────────────────

    def clear(self) -> None:
        self._short_term.clear()
        self._long_term.clear()

    def snapshot(self) -> dict[str, Any]:
        return {
            "short_term_size": len(self._short_term),
            "long_term_keys": list(self._long_term.keys()),
            "long_term_size": len(self._long_term),
        }

    def __len__(self) -> int:
        return len(self._short_term) + len(self._long_term)
