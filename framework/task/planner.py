"""Planner — 任务拆解器（v0.5）。

从关键词规则升级为 Orchestrator Agent 驱动。
Orchestrator 负责拆解 + Agent 选择，Planner 负责将计划翻译为 Task/Subtask 数据结构。
"""

from __future__ import annotations

import time
from typing import Any

from .task import Task, Subtask, TaskStatus
from ..agent.registry import AgentRegistry
from ..skill.registry import SkillRegistry
from ..orchestrator.orchestrator import OrchestratorAgent


class Planner:
    """任务规划器——调用 Orchestrator 拆解，翻译为 Task 对象。"""

    def __init__(
        self,
        agent_registry: AgentRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        orchestrator: OrchestratorAgent | None = None,
    ):
        self._agent_registry = agent_registry or AgentRegistry()
        self._skill_registry = skill_registry or SkillRegistry()
        self._orchestrator = orchestrator or OrchestratorAgent()

    async def plan(self, user_input: str) -> Task:
        """调用 Orchestrator 拆解用户输入为 DAG 子任务。

        Orchestrator 输出 → Planner 翻译 → Task + Subtask 对象。
        """
        task = Task(
            description=user_input,
            status=TaskStatus.PLANNING,
            created_at=time.time(),
        )

        # 构建 Agent 目录
        catalog = self._orchestrator.build_catalog_from_registries(
            self._agent_registry
        )

        # 调 Orchestrator（纯代码模式或 LLM 模式，取决于 adapter 配置）
        plan = await self._orchestrator.plan(user_input, catalog)

        # 翻译为 Subtask 对象
        for st_data in plan.get("tasks", []):
            agent_type = st_data.get("agent", "")
            description = st_data.get("description", "") or user_input
            input_data = st_data.get("input")
            input_data = dict(input_data) if isinstance(input_data, dict) else {}
            input_data.setdefault("query", description)
            if agent_type == "code_agent":
                input_data.setdefault("task_description", description)
            subtask = Subtask(
                subtask_id=st_data.get("subtask_id", ""),
                description=description,
                agent_type=agent_type,
                skill_name=st_data.get("skill", ""),
                input_data=input_data,
                depends_on=st_data.get("depends_on", []),
            )
            task.subtasks.append(subtask)

        if not task.subtasks:
            # If planning failed, preserve the user's request for the general worker.
            task.subtasks.append(Subtask(
                description=user_input,
                agent_type="general_agent",
                skill_name="bash",
                input_data={"query": user_input},
            ))

        task.status = TaskStatus.DISPATCHED
        return task
