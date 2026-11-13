"""SkillRegistry v0.7 — 能力市场 + contract 匹配 + 多维度评分。

按 contract 动态匹配，支持 cost/latency/quality 策略选择 + 复合评分。
1000 个 Skill 时主 Agent 不可能全部记住——靠 Registry 检索。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .base import Skill


@dataclass
class MatchResult:
    """匹配结果——包含 Skill 和评分详情。"""
    skill: Skill
    capability_coverage: float  # 0–1，能力覆盖度
    input_match: float          # 0–1，输入匹配度
    composite_score: float      # 0–1，综合评分
    scores: dict[str, float] = field(default_factory=dict)


class SkillRegistry:
    """能力市场——存储、索引、匹配、评分。"""

    def __init__(self):
        self._skills: dict[str, Skill] = {}
        self._index: dict[str, list[str]] = {}  # capability → [skill_name, ...]

    # ── CRUD ──────────────────────────────────

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill
        for cap in skill.contract.capabilities:
            self._index.setdefault(cap, []).append(skill.name)

    def unregister(self, name: str) -> None:
        skill = self._skills.pop(name, None)
        if skill:
            for cap in skill.contract.capabilities:
                names = self._index.get(cap, [])
                if name in names:
                    names.remove(name)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[str]:
        return list(self._skills.keys())

    def find_by_capability(self, capability: str) -> list[Skill]:
        names = self._index.get(capability, [])
        return [self._skills[n] for n in names if n in self._skills]

    # ── v0.7: Contract Matching + Scoring ─────

    def select(
        self,
        required_capabilities: list[str],
        required_inputs: list[str] | None = None,
        optimize: str = "quality",
    ) -> Skill | None:
        """选择最佳 Skill（保留向后兼容的简单接口）。"""
        results = self.rank(required_capabilities, required_inputs, optimize, top_n=1)
        return results[0].skill if results else None

    def rank(
        self,
        required_capabilities: list[str],
        available_inputs: list[str] | None = None,
        optimize: str = "quality",
        top_n: int = 5,
    ) -> list[MatchResult]:
        """按 contract 匹配 + 评分排序，返回 top_n。

        Args:
            required_capabilities: 必须覆盖的能力标签
            available_inputs: 调用方能提供的输入字段
            optimize: "quality" / "cost" / "latency" / "balanced"
            top_n: 返回前 N 个

        Returns:
            按评分降序排列的匹配结果列表。
        """
        results: list[MatchResult] = []

        for skill in self._skills.values():
            caps = set(skill.contract.capabilities)
            required = set(required_capabilities)

            # 能力覆盖度
            coverage = len(required & caps) / len(required) if required else 1.0
            if coverage == 0:
                continue

            # 输入匹配度
            input_match = 1.0
            if available_inputs:
                reqs = set(skill.contract.requires)
                available = set(available_inputs)
                input_match = len(reqs & available) / len(reqs) if reqs else 1.0

            # 综合评分
            composite = self._composite_score(skill, coverage, input_match, optimize)

            results.append(MatchResult(
                skill=skill,
                capability_coverage=coverage,
                input_match=input_match,
                composite_score=composite,
                scores={
                    "cost": skill.contract.cost_score,
                    "latency": skill.contract.latency_score,
                    "quality": skill.contract.quality_score,
                    "coverage": coverage,
                    "input_match": input_match,
                },
            ))

        results.sort(key=lambda r: r.composite_score, reverse=True)
        return results[:top_n]

    def _composite_score(
        self,
        skill: Skill,
        coverage: float,
        input_match: float,
        optimize: str,
    ) -> float:
        """计算综合评分——组合 cost/latency/quality + 匹配度。"""
        c = skill.contract
        weights = {
            "quality":  (0.1, 0.1, 0.6, 0.1, 0.1),
            "cost":     (0.4, 0.1, 0.2, 0.2, 0.1),
            "latency":  (0.1, 0.4, 0.2, 0.2, 0.1),
            "balanced": (0.2, 0.2, 0.3, 0.15, 0.15),
        }.get(optimize, (0.1, 0.1, 0.6, 0.1, 0.1))

        w_cost, w_lat, w_qual, w_cov, w_inp = weights

        return (
            w_cost  * (1 - c.cost_score) +    # cost 低 → 高分
            w_lat   * (1 - c.latency_score) +  # latency 低 → 高分
            w_qual  * c.quality_score +         # quality 高 → 高分
            w_cov   * coverage +
            w_inp   * input_match
        )

    def get_stats(self) -> dict:
        """Registry 统计信息。"""
        return {
            "skill_count": len(self._skills),
            "capability_count": len(self._index),
            "skills": {
                name: {
                    "capabilities": s.contract.capabilities,
                    "scores": {
                        "cost": s.contract.cost_score,
                        "latency": s.contract.latency_score,
                        "quality": s.contract.quality_score,
                    },
                }
                for name, s in self._skills.items()
            },
        }

    def __len__(self) -> int:
        return len(self._skills)

    def __repr__(self) -> str:
        return f"<SkillRegistry v0.7: {len(self._skills)} skills, {len(self._index)} capabilities>"
