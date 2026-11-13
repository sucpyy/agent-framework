"""AggregatorPipeline — 串联 Validator → Merger → Synthesizer。

完整的三层聚合管道。
"""

from __future__ import annotations

from typing import Any

from .validator import SchemaValidator
from .merger import StructuredMerger
from .synthesizer import LLMSynthesizer


class AggregatorPipeline:
    """三层聚合管道。

    用法:
        pipeline = AggregatorPipeline()
        result = await pipeline.run(
            worker_results,
            expected_fields=["summary", "sources"],
            task_description="...",
        )
    """

    def __init__(
        self,
        validator: SchemaValidator | None = None,
        merger: StructuredMerger | None = None,
        synthesizer: LLMSynthesizer | None = None,
    ):
        self.validator = validator or SchemaValidator()
        self.merger = merger or StructuredMerger()
        self.synthesizer = synthesizer or LLMSynthesizer()

    async def run(
        self,
        worker_results: list[dict[str, Any]],
        expected_fields: list[str] | None = None,
        task_description: str = "",
        *,
        skip_synthesis: bool = False,
    ) -> dict[str, Any]:
        """执行完整聚合管道。

        Args:
            worker_results: Worker Agent 的原始返回
            expected_fields: 预期输出字段（从 contract.produces）
            task_description: 原始任务描述（供 LLM 综述用）
            skip_synthesis: 跳过 LLM 综述

        Returns:
            {
                "passed": [...],       # 通过校验的结果
                "failed": [...],       # 校验失败的结果
                "merged": {...},       # 合并后的结果
                "synthesis": "...",    # LLM 综述（可选）
                "stats": {...},        # 统计信息
            }
        """
        # Layer 1: Schema Validation
        if expected_fields:
            passed, failed = self.validator.validate_batch(
                worker_results, expected_fields
            )
        else:
            passed = worker_results
            failed = []

        # Layer 2: Structured Merge
        merged = self.merger.merge(passed)
        merged["_types"] = list(set(r.get("type", "unknown") for r in passed))
        merged["_count"] = len(passed)
        merged["_raw"] = passed

        # Layer 3: LLM Synthesis (optional)
        synthesis = ""
        if not skip_synthesis and passed:
            synthesis = await self.synthesizer.synthesize(
                merged, task_description
            )

        return {
            "passed": passed,
            "failed": failed,
            "merged": merged,
            "synthesis": synthesis,
            "stats": {
                "total": len(worker_results),
                "passed": len(passed),
                "failed": len(failed),
                "synthesis_used": bool(synthesis),
                "llm_calls_saved": len(passed) - (1 if synthesis else 0),
            },
        }
