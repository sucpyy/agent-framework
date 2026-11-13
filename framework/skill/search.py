"""SearchSkill — 搜索能力。

Anthropic tool schema + 执行逻辑。
"""

from __future__ import annotations

from typing import Any

from .base import Skill, SkillContract
from .web_search import WebSearchSkill

SEARCH_CONTRACT = SkillContract(
    name="search",
    capabilities=["web_search", "information_retrieval", "summarize"],
    requires=["query"],
    produces=["summary", "sources", "relevance_score"],
    cost_score=0.2,
    latency_score=0.7,
    quality_score=0.85,
    verified_outputs={
        "summary": "Relevant information summary...",
        "sources": ["source1", "source2"],
        "relevance_score": 0.92,
    },
)


class SearchSkill(Skill):
    """搜索技能。"""

    def __init__(self):
        super().__init__(SEARCH_CONTRACT)
        self._web_search = WebSearchSkill()

    # ── Anthropic Tool Schema ─────────────────

    def to_tool_schema(self) -> dict[str, Any]:
        return {
            "name": "web_search",
            "description": (
                "Search the web for information on a given query. "
                "Returns a structured summary with sources and relevance score. "
                "Use this when you need up-to-date information, facts, or research."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query. Be specific and include relevant keywords.",
                    },
                },
                "required": ["query"],
            },
        }

    # ── Execution ────────────────────────────

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        if not self.validate_input(**kwargs):
            missing = [k for k in self.contract.requires if k not in kwargs]
            return {
                "summary": "",
                "sources": [],
                "relevance_score": 0.0,
                "error": f"Missing required inputs: {missing}",
            }

        query = kwargs["query"]
        result = await self._web_search.execute(query=query, max_results=kwargs.get("max_results", 5))
        entries = result.get("results", [])
        summary = result.get("answer", "") or "\n".join(
            f"- {item.get('title', '搜索结果')}: {item.get('content', '')}" for item in entries
        )
        result = {
            "summary": summary,
            "sources": [item.get("url", "") for item in entries if item.get("url")],
            "relevance_score": max((float(item.get("score", 0) or 0) for item in entries), default=0),
            "provider": result.get("provider"),
            **({"error": result["error"]} if result.get("error") else {}),
        }
        assert self.validate_output(result), "SearchSkill output contract violated"
        return result
