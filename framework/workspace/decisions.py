"""Decisions — 设计决策记录。

记录"为什么这样设计/为什么这样修改"。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Decision:
    """单条设计决策。"""
    decision_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    context: str = ""        # 当时在做什么
    decision: str = ""       # 做了什么决定
    reason: str = ""         # 为什么
    alternatives: list[str] = field(default_factory=list)  # 其他考虑过的方案
    timestamp: float = field(default_factory=time.time)


class Decisions:
    """设计决策日志——存储和检索设计决策。"""

    def __init__(self):
        self._decisions: list[Decision] = []

    def record(
        self,
        context: str,
        decision: str,
        reason: str,
        alternatives: list[str] | None = None,
    ) -> Decision:
        d = Decision(
            context=context,
            decision=decision,
            reason=reason,
            alternatives=alternatives or [],
        )
        self._decisions.append(d)
        return d

    def list_all(self) -> list[Decision]:
        return list(self._decisions)

    def find_by_context(self, keyword: str) -> list[Decision]:
        return [d for d in self._decisions if keyword.lower() in d.context.lower()]

    def clear(self) -> None:
        self._decisions.clear()

    def __len__(self) -> int:
        return len(self._decisions)
