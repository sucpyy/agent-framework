"""LLM Synthesizer — 仅在需要洞察/综述时才调 LLM。

这是三层中的最后一层：
  Validator → Merger → Synthesizer（optional）

大多数 Framework 跳过前两层直接调 LLM。
Validator + Merger 可降 60-80% LLM 调用量。
"""

from __future__ import annotations

from typing import Any

from ..sdk.adapter import ClaudeAdapter


SYNTHESIS_PROMPT = """You are a synthesis engine. Your job is to read structured task results and produce a concise executive summary.

CRITICAL: You MUST write in Chinese (中文) only.

Rules:
1. Focus on insights, not data repetition
2. Highlight conflicts or agreements between results
3. Be concise — 3-5 sentences max
4. Do NOT repeat raw data that's already available in the structured fields
5. If results are from different domains (e.g. search + code), explain how they connect"""


class LLMSynthesizer:
    """LLM 驱动的综述生成——仅在需要时调用。"""

    def __init__(self, adapter: ClaudeAdapter | None = None):
        self._adapter = adapter or ClaudeAdapter()

    def needs_synthesis(self, merged_result: dict[str, Any]) -> bool:
        """判断是否需要 LLM 综述。

        以下情况不需要 LLM:
        - 单一类型的单条结果
        - 所有结果都来自同一个 Skill
        - 结果已经很简单（短文本）
        """
        types = merged_result.get("_types", [])
        count = merged_result.get("_count", 0)

        if count <= 1 and len(types) <= 1:
            return False

        if count >= 2 and len(types) >= 2:
            return True  # 跨域结果，需要综述

        return False

    async def synthesize(
        self,
        merged_result: dict[str, Any],
        task_description: str,
    ) -> str:
        """生成 LLM 综述。"""
        if not self.needs_synthesis(merged_result):
            return ""

        result = await self._adapter.run(
            system_prompt=SYNTHESIS_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Task: {task_description}\n\n"
                    f"Results: {self._format_for_synthesis(merged_result)}\n\n"
                    f"Provide a concise executive summary."
                ),
            }],
            temperature=0.3,
            max_tokens=512,
        )

        return result["content"]

    def _format_for_synthesis(self, merged: dict[str, Any]) -> str:
        """格式化合并结果供 LLM 阅读。"""
        parts = []
        skip_keys = {"_types", "_count", "_raw"}
        for key, value in merged.items():
            if key in skip_keys:
                continue
            if isinstance(value, str) and len(value) > 500:
                value = value[:500] + "..."
            parts.append(f"{key}: {value}")
        return "\n".join(parts)
