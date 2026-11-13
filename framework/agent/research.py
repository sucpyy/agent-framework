"""ResearchAgent — 研究型 Worker Agent。

架构约束: Worker 不调用其他 Agent，由 Scheduler 统一调度。
"""

from __future__ import annotations
import asyncio
from typing import Any
from .base import Agent, AgentContext
from ..skill.web_search import WebSearchSkill
from ..sdk.adapter import ClaudeAdapter

RESEARCH_PROMPT = """You are a Research Agent. Your job is to gather, analyze, and synthesize information.

## Your Tool
- **web_search**: Search the internet for information
- **search_and_fetch**: Search and fetch the best result pages

## Rules
1. Use web_search to gather information
2. Synthesize results into a clear, structured summary
3. Cite all sources
4. CRITICAL: ALL responses MUST be in Chinese (中文)
5. Focus ONLY on your assigned subtask — do NOT try to delegate to other agents"""


class ResearchAgent(Agent):
    """研究型 Worker——仅持有 SearchSkill，不调用其他 Agent。"""

    SUMMARY_TIMEOUT = 8.0

    def __init__(self, adapter: ClaudeAdapter | None = None):
        self._search_skill = WebSearchSkill()
        super().__init__(
            name="research_agent",
            skills=[self._search_skill],
            system_prompt=RESEARCH_PROMPT,
            memory_scope=10,
        )
        self._adapter = adapter or ClaudeAdapter()

    def set_registry(self, registry):
        pass  # Worker 不需要 registry

    def get_all_tool_schemas(self) -> list[dict]:
        return self._search_skill.to_tool_schemas()

    def get_all_tool_handlers(self) -> dict:
        return {"web_search":self._search_skill.execute,"search_and_fetch":self._search_skill.execute_fetch}

    async def execute(self, context: AgentContext) -> dict[str, Any]:
        query = str(context.input_data.get("query", context.input_data.get("description", ""))).strip()
        search_output = await self._search_skill.execute(query=query)
        results = search_output.get("results", [])
        summary = search_output.get("answer", "") or "\n".join(
            f"- {item.get('title', '搜索结果')}: {item.get('content', '')}" for item in results[:5]
        )
        error = search_output.get("error")
        usage = {}
        if results and self._adapter.mode == "live":
            try:
                model_result = await asyncio.wait_for(self._adapter.run(
                    system_prompt="只依据提供的搜索结果，用中文简洁总结。不要编造来源或未提供的事实。",
                    messages=[{"role": "user", "content": f"问题：{query}\n搜索结果：{summary[:3500]}"}],
                    max_tokens=512, role="research",
                ), timeout=self.SUMMARY_TIMEOUT)
                summary = model_result.get("content") or summary
                usage = model_result.get("usage", {})
            except Exception:
                pass  # The verified search results remain available when summarization stalls.
        return {
            "agent": self.name, "task_id": context.task_id,
            "type": "research_result",
            "summary": summary or error or "未找到相关结果。",
            "sources": [item.get("url", "") for item in results if item.get("url")],
            "error": error,
            "claude_response": summary,
            "usage": usage, "mode": self._adapter.mode,
        }
