"""Structured Aggregator — 降 Token 核心机制。

Worker Results (raw)
  → Schema Validator（校验是否符合 contract.produces）
  → Structured Merger（按 schema 合并，纯代码）
  → LLM Synthesis（只在需要洞察/综述时才调 LLM）
"""

from .validator import SchemaValidator
from .merger import StructuredMerger
from .synthesizer import LLMSynthesizer
from .pipeline import AggregatorPipeline

__all__ = ["SchemaValidator", "StructuredMerger", "LLMSynthesizer", "AggregatorPipeline"]
