"""Skill = 单一能力单元（tool + schema + contract）

Agent 通过 contract 匹配 Skill，用户不直接调 Skill。
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillContract:
    """Skill 的能力声明——Agent 和 Registry 通过它匹配。"""

    name: str
    capabilities: list[str]  # 能做什么（自然语言标签）
    requires: list[str]      # 输入必须提供的字段
    produces: list[str]      # 输出会包含的字段
    cost_score: float = 0.5   # 0–1，越低越省钱
    latency_score: float = 0.5  # 0–1，越低越快
    quality_score: float = 0.5  # 0–1，越高越好
    verified_outputs: dict[str, Any] = field(default_factory=dict)  # sample verified output

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capabilities": self.capabilities,
            "requires": self.requires,
            "produces": self.produces,
            "cost_score": self.cost_score,
            "latency_score": self.latency_score,
            "quality_score": self.quality_score,
            "verified_outputs": self.verified_outputs,
        }


class Skill(ABC):
    """技能抽象基类——每种具体能力继承它。"""

    def __init__(self, contract: SkillContract):
        self.contract = contract

    @property
    def name(self) -> str:
        return self.contract.name

    @abstractmethod
    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """执行技能，返回结构化结果。必须符合 contract.produces。"""
        ...

    def validate_input(self, **kwargs: Any) -> bool:
        """校验输入是否满足 contract.requires。"""
        return all(key in kwargs for key in self.contract.requires)

    def validate_output(self, result: dict[str, Any]) -> bool:
        """校验输出是否满足 contract.produces。"""
        return all(key in result for key in self.contract.produces)

    def __repr__(self) -> str:
        caps = ", ".join(self.contract.capabilities)
        return f"<Skill {self.name}: {caps}>"
