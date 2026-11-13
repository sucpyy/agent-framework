"""EventBus — 运行时事件总线。

解耦组件通信：Emitter 不关心 Listener 是谁。
Agent 最大的问题不是失败，而是为什么失败——EventBus 是回答这个问题的第一步。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, Callable, Coroutine


Handler = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """轻量级异步事件总线。"""

    def __init__(self):
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._event_log: list[dict[str, Any]] = []

    def on(self, event: str, handler: Handler) -> None:
        """订阅事件。"""
        self._handlers[event].append(handler)

    def off(self, event: str, handler: Handler) -> None:
        """取消订阅。"""
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """发射事件——异步通知所有订阅者。"""
        entry = {
            "timestamp": time.time(),
            "event": event,
            "payload": payload or {},
        }
        self._event_log.append(entry)

        handlers = self._handlers.get(event, [])
        if not handlers:
            return

        tasks = [handler(**entry) for handler in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)

    def drain(self) -> list[dict[str, Any]]:
        """取出当前事件日志并清空。"""
        log = list(self._event_log)
        self._event_log.clear()
        return log

    def clear(self) -> None:
        self._event_log.clear()
        self._handlers.clear()
