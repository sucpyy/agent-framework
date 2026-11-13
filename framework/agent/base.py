"""Agent = Reasoning Loop + Skill Set + Memory Scope

两层注册：用户不直接调 Skill，通过 Agent。Agent 决定用哪个 Skill、何时用。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..skill.base import Skill


def tool_failure_message(tool_calls: list[dict[str, Any]]) -> str | None:
    """Report a failed task when every invoked tool failed."""
    if not tool_calls:
        return None
    failures = []
    for call in tool_calls:
        output = call.get("output")
        if not isinstance(output, dict):
            continue
        error = output.get("error")
        if not error and output.get("status") in {"error", "send_failed"}:
            error = output["status"]
        if not error and output.get("exit_code", 0) != 0:
            error = output.get("stderr") or f"退出码 {output['exit_code']}"
        if error:
            failures.append(str(error))
    return "; ".join(failures) if len(failures) == len(tool_calls) else None


@dataclass
class AgentContext:
    """Agent 单次执行上下文。"""
    agent_name: str
    task_id: str
    input_data: dict[str, Any]
    start_time: float = field(default_factory=time.time)


class Agent(ABC):
    """Agent 抽象基类。动态创建的 Agent 使用默认 execute。

    Agent  = system_prompt + skill_set + memory_scope
    """

    def __init__(
        self,
        name: str,
        skills: list[Skill],
        system_prompt: str,
        memory_scope: int = 10,
    ):
        self.name = name
        self._skills: dict[str, Skill] = {s.name: s for s in skills}
        self.system_prompt = system_prompt
        self.memory_scope = memory_scope

    @property
    def skill_names(self) -> list[str]:
        return list(self._skills.keys())

    def has_skill(self, name: str) -> bool:
        return name in self._skills

    def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    async def execute(self, context: AgentContext) -> dict[str, Any]:
        """默认执行——使用 ClaudeAdapter 调用。子类可覆盖。"""
        from ..sdk.openai_adapter import OpenAIAdapter
        adapter = OpenAIAdapter(model="deepseek-chat")
        msg = context.input_data.get("query", context.input_data.get("description", ""))
        tools = [s.to_tool_schema() for s in self._skills.values() if hasattr(s, 'to_tool_schema')]
        handlers = {s.contract.name: s.execute for s in self._skills.values()}
        result = await adapter.run(
            system_prompt=self.system_prompt,
            messages=[{"role": "user", "content": msg}],
            tools=tools or None,
            tool_handlers=handlers or None,
            temperature=0.3, max_tokens=4096,
        )
        return {
            "agent": self.name, "task_id": context.task_id, "type": "agent_result",
            "response": result["content"],
            "tool_calls": [{"name": tc["name"], "input": tc["input"], "output": tc.get("output", {})}
                           for tc in result.get("tool_calls", [])],
            "usage": result.get("usage", {}), "mode": result.get("mode", "echo"),
        }

    def __repr__(self) -> str:
        skills = ", ".join(self.skill_names)
        return f"<Agent {self.name}: [{skills}]>"
