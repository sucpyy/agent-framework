"""Context — 当前会话的上下文窗口。

维护最近消息、活跃任务、焦点。
"""

from __future__ import annotations

from collections import deque
from typing import Any


class Context:
    """会话上下文——最近消息 + 活跃任务 + 焦点。"""

    def __init__(self, max_messages: int = 50):
        self._messages: deque[dict[str, Any]] = deque(maxlen=max_messages)
        self._active_task_ids: list[str] = []
        self._focus: str | None = None  # 当前焦点 task_id

    def add_message(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})

    def recent_messages(self, n: int | None = None) -> list[dict[str, Any]]:
        items = list(self._messages)
        if n is not None:
            items = items[-n:]
        return items

    @property
    def active_tasks(self) -> list[str]:
        return list(self._active_task_ids)

    def activate_task(self, task_id: str) -> None:
        if task_id not in self._active_task_ids:
            self._active_task_ids.append(task_id)

    def deactivate_task(self, task_id: str) -> None:
        if task_id in self._active_task_ids:
            self._active_task_ids.remove(task_id)

    @property
    def focus(self) -> str | None:
        return self._focus

    def set_focus(self, task_id: str | None) -> None:
        self._focus = task_id

    def snapshot(self) -> dict[str, Any]:
        return {
            "messages": list(self._messages),
            "active_tasks": self._active_task_ids,
            "focus": self._focus,
        }

    def restore(self, state: dict[str, Any]) -> None:
        self._messages = deque(state.get("messages", []), maxlen=self._messages.maxlen)
        self._active_task_ids = state.get("active_tasks", [])
        self._focus = state.get("focus")
