"""Auto-Evaluator — 自动评分 Agent 输出质量

维度: correctness, completeness, efficiency, tone
"""

from __future__ import annotations
import json, time
from typing import Any
from ..sdk.openai_adapter import OpenAIAdapter

EVAL_PROMPT = """You are an output evaluator. Score the following agent response.

Criteria (1-10 each):
- correctness: Is the information accurate and free of errors?
- completeness: Does it fully address the task?
- efficiency: Is it concise without missing key points?
- tone: Is the tone appropriate (professional, helpful)?

Return ONLY a JSON: {"correctness": N, "completeness": N, "efficiency": N, "tone": N, "overall": N, "notes": "brief comment"}"""


class AutoScorer:
    def __init__(self, adapter: OpenAIAdapter | None = None):
        self._adapter = adapter or OpenAIAdapter(model="deepseek-chat")

    async def score(self, task: str, response: str) -> dict:
        """评分 Agent 输出。Echo 模式返回默认分。"""
        if self._adapter.mode == "echo":
            return {"correctness": 8, "completeness": 8, "efficiency": 8, "tone": 8,
                    "overall": 8, "notes": "echo mode"}

        result = await self._adapter.run(
            system_prompt=EVAL_PROMPT,
            messages=[{"role": "user", "content": f"Task: {task}\n\nResponse: {response[:2000]}"}],
            temperature=0.1, max_tokens=200,
        )
        try:
            return json.loads(result["content"])
        except json.JSONDecodeError:
            return {"correctness": 5, "completeness": 5, "efficiency": 5, "tone": 5,
                    "overall": 5, "notes": "parse error"}

    async def score_batch(self, tasks_and_responses: list[tuple[str, str]]) -> list[dict]:
        scores = []
        for task, resp in tasks_and_responses:
            scores.append(await self.score(task, resp))
        return scores
