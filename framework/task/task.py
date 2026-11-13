"""Task + Subtask = 工作单元。

Task 是状态变迁的触发器，Workspace Session 才是长期存在的一等公民。
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    PLANNING = "planning"
    DISPATCHED = "dispatched"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Subtask:
    """子任务——Planner 拆解产物，对应一个 Agent 的一次执行。"""

    subtask_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""
    agent_type: str = ""        # 匹配的 Agent 名称
    skill_name: str = ""        # 需要的 Skill
    input_data: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)  # DAG 依赖
    status: TaskStatus = TaskStatus.PENDING
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class Task:
    """顶级任务——用户输入经 Planner 拆解后的完整工作单元。"""

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""
    subtasks: list[Subtask] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = 0.0
    completed_at: float | None = None
    checkpoint_id: str | None = None

    @property
    def is_complete(self) -> bool:
        return all(
            st.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for st in self.subtasks
        ) if self.subtasks else self.status == TaskStatus.COMPLETED

    @property
    def completed_count(self) -> int:
        return sum(1 for st in self.subtasks if st.status == TaskStatus.COMPLETED)

    @property
    def failed_count(self) -> int:
        return sum(1 for st in self.subtasks if st.status == TaskStatus.FAILED)


@dataclass
class TaskResult:
    """任务聚合结果。"""
    task_id: str
    status: TaskStatus
    subtask_results: list[dict[str, Any]]
    aggregated_output: str = ""
    total_latency: float = 0.0
