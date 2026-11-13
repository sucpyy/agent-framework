"""Workspace v1.0 — 第一公民 + 全量持久化。

Workspace 内部结构:
  Context / Artifacts / Tasks / Decisions / Memory
  / Skills / Agents / EventLog / Checkpoints
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .context import Context
from .artifacts import Artifacts
from .decisions import Decisions, Decision
from ..task.task import Task, TaskStatus
from ..memory.memory import Memory
from ..skill.registry import SkillRegistry
from ..agent.registry import AgentRegistry
from ..event.log import EventLog
from ..event.bus import EventBus
from ..checkpoint.checkpoint import Checkpoint, CheckpointStore


class Workspace:
    """Workspace Session — Agent Framework 第一公民。"""

    def __init__(
        self,
        name: str = "default",
        checkpoint_dir: str = ".checkpoints",
        persist_dir: str | None = None,
    ):
        self.name = name
        self.context = Context()
        self.artifacts = Artifacts()
        self.tasks: list[Task] = []
        self.decisions = Decisions()
        self.memory = Memory(
            long_term_path=f"{persist_dir}/long_term_memory.json" if persist_dir else None,
        )
        self.skills = SkillRegistry()
        self.agents = AgentRegistry()
        self.event_log = EventLog(
            file_path=f"{persist_dir}/event_log.json" if persist_dir else None,
        )
        self.event_bus = EventBus()
        self.checkpoints = CheckpointStore(checkpoint_dir)

        self._created_at = time.time()
        self._persist_dir = Path(persist_dir) if persist_dir else None

        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_state()

        self._setup_listeners()

    # ── Event Listeners ───────────────────────

    def _setup_listeners(self) -> None:
        async def log_event(**kwargs: Any) -> None:
            event = kwargs.get("event", "unknown")
            payload = kwargs.get("payload", {})
            self.event_log.append(event, payload)
            self.memory.add({"event": event, "payload": payload})

        for evt in (
            "task:created", "task:dispatched", "task:completed", "task:failed",
            "subtask:started", "subtask:completed", "subtask:timeout",
            "skill:invoked", "decision:made",
        ):
            self.event_bus.on(evt, log_event)

    # ── Task 生命周期 ──────────────────────────

    async def submit_task(self, task: Task) -> None:
        self.tasks.append(task)
        self.context.activate_task(task.task_id)
        await self.event_bus.emit("task:created", {
            "task_id": task.task_id,
            "description": task.description,
            "subtask_count": len(task.subtasks),
        })

    async def complete_task(self, task: Task) -> None:
        task.status = TaskStatus.COMPLETED
        task.completed_at = time.time()
        self.context.deactivate_task(task.task_id)
        await self.event_bus.emit("task:completed", {
            "task_id": task.task_id,
            "completed": task.completed_count,
            "failed": task.failed_count,
        })

    # ── Decisions ─────────────────────────────

    async def record_decision(
        self, context: str, decision: str, reason: str
    ) -> Decision:
        d = self.decisions.record(context, decision, reason)
        await self.event_bus.emit("decision:made", {
            "context": context, "decision": decision, "reason": reason,
        })
        return d

    # ── Checkpoint ────────────────────────────

    async def save_checkpoint(self, task: Task) -> Checkpoint:
        cp = Checkpoint(
            task_id=task.task_id,
            subtask_states={st.subtask_id: st.status.value for st in task.subtasks},
            workspace_snapshot=self.snapshot(),
            data={
                "context": self.context.snapshot(),
                "task_description": task.description,
            },
        )
        self.checkpoints.save(cp)
        task.checkpoint_id = cp.checkpoint_id
        return cp

    async def resume(self, checkpoint_id: str) -> Checkpoint | None:
        cp = self.checkpoints.load(checkpoint_id)
        if cp and cp.workspace_snapshot:
            self.context.restore(cp.data.get("context", {}))
        return cp

    # ── Persistence ───────────────────────────

    def save(self) -> None:
        """全量持久化 Workspace 状态到磁盘。"""
        if not self._persist_dir:
            return
        state = {
            "name": self.name,
            "created_at": self._created_at,
            "context": self.context.snapshot(),
            "task_count": len(self.tasks),
            "decisions": [d.__dict__ for d in self.decisions.list_all()],
            "memory_snapshot": self.memory.snapshot(),
        }
        (self._persist_dir / "workspace_state.json").write_text(
            json.dumps(state, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        self.event_log.save()

    def _load_state(self) -> None:
        """从磁盘恢复 Workspace 状态。"""
        state_file = self._persist_dir / "workspace_state.json" if self._persist_dir else None
        if not state_file or not state_file.exists():
            return
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            self.name = data.get("name", self.name)
            self._created_at = data.get("created_at", self._created_at)
            self.context.restore(data.get("context", {}))
        except (json.JSONDecodeError, FileNotFoundError):
            pass
        self.event_log.load()

    # ── Queries ───────────────────────────────

    def replay(self) -> list[dict[str, Any]]:
        return self.event_log.replay()

    def event_counts(self) -> dict[str, int]:
        return self.event_log.count_by_event()

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "created_at": self._created_at,
            "uptime": f"{self.uptime:.0f}s",
            "context": self.context.snapshot(),
            "task_count": len(self.tasks),
            "artifact_count": len(self.artifacts),
            "decision_count": len(self.decisions),
            "memory": self.memory.snapshot(),
            "skill_count": len(self.skills),
            "agent_count": len(self.agents),
            "event_count": len(self.event_log),
            "checkpoint_count": len(self.checkpoints),
        }

    @property
    def uptime(self) -> float:
        return time.time() - self._created_at
