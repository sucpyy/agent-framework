"""AgentRegistry — 推理单元注册中心。

存储所有 Agent，按任务需求匹配最佳 Agent。
"""

from __future__ import annotations

from .base import Agent


class AgentRegistry:
    """Agent 注册中心——管理和匹配推理单元。"""

    def __init__(self):
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        self._agents[agent.name] = agent

    def unregister(self, name: str) -> None:
        self._agents.pop(name, None)

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    def list_all(self) -> list[str]:
        return list(self._agents.keys())

    def find_by_skill(self, skill_name: str) -> list[Agent]:
        """找到所有拥有指定 Skill 的 Agent。"""
        return [a for a in self._agents.values() if a.has_skill(skill_name)]

    def find_by_capability(self, capability: str) -> list[Agent]:
        """找到所有能处理某种能力的 Agent。"""
        result: list[Agent] = []
        for agent in self._agents.values():
            for skill in agent._skills.values():
                if capability in skill.contract.capabilities:
                    result.append(agent)
                    break
        return result

    def __len__(self) -> int:
        return len(self._agents)

    def __repr__(self) -> str:
        return f"<AgentRegistry: {len(self._agents)} agents>"
