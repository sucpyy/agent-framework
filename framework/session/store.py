"""Session Store — 会话持久化

保存/加载/恢复完整对话会话，支持多会话管理。
"""

from __future__ import annotations
import json, os, re, tempfile, time, uuid
from pathlib import Path
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[dict] = field(default_factory=list)  # [{role, content, tool_calls?}]
    agent_states: dict[str, str] = field(default_factory=dict)  # agent → status
    metadata: dict = field(default_factory=dict)

    def add_message(self, role: str, content: str, tool_calls: list | None = None):
        msg = {"role": role, "content": content, "ts": time.time()}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id, "name": self.name,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "messages": self.messages, "agent_states": self.agent_states,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            session_id=d.get("session_id", ""), name=d.get("name", ""),
            created_at=d.get("created_at", 0), updated_at=d.get("updated_at", 0),
            messages=d.get("messages", []), agent_states=d.get("agent_states", {}),
            metadata=d.get("metadata", {}),
        )


class SessionStore:
    def __init__(self, base_dir: str = ".sessions"):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._current: Session | None = None
        self._lock = RLock()

    def _path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{8}", session_id):
            raise ValueError("Invalid session_id")
        path = self._base / f"{session_id}.json"
        if path.resolve().parent != self._base.resolve():
            raise ValueError("Invalid session path")
        return path

    def create(self, name: str = "") -> Session:
        s = Session(name=name or f"Session-{time.strftime('%m%d-%H%M')}")
        self._current = s
        return s

    def save(self, session: Session | None = None):
        s = session or self._current
        if not s: return
        path = self._path(s.session_id)
        with self._lock:
            # Replace atomically so readers never see a half-written conversation.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._base,
                                                 suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(s.to_dict(), stream, indent=2, ensure_ascii=False)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def load(self, session_id: str) -> Session | None:
        path = self._path(session_id)
        if not path.exists(): return None
        s = Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if s.session_id != session_id:
            raise ValueError("Stored session_id does not match its file")
        self._current = s
        return s

    def append_message(self, session_id: str, role: str, content: str,
                       tool_calls: list | None = None) -> Session | None:
        """Read the latest session when appending, preserving concurrent edits."""
        with self._lock:
            session = self.load(session_id)
            if session is None:
                return None
            session.add_message(role, content, tool_calls)
            self.save(session)
            return session

    def rename(self, session_id: str, name: str) -> Session | None:
        with self._lock:
            session = self.load(session_id)
            if session is None:
                return None
            session.name = name
            session.updated_at = time.time()
            self.save(session)
            return session

    def list_all(self) -> list[dict]:
        sessions = []
        for p in sorted(self._base.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": d.get("session_id"), "name": d.get("name"),
                    "created_at": d.get("created_at"), "updated_at": d.get("updated_at"),
                    "message_count": len(d.get("messages", [])),
                })
            except: pass
        return sessions

    def delete(self, session_id: str):
        with self._lock:
            self._path(session_id).unlink(missing_ok=True)
            if self._current and self._current.session_id == session_id:
                self._current = None

    @property
    def current(self) -> Session | None:
        return self._current
