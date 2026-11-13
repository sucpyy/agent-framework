"""Evaluation v1.0 — 指标收集、追踪、报告、自动评分。

re-exports from tracker.py (MetricsTracker) and scorer.py (AutoScorer).
"""

from .tracker import MetricsTracker, RunMetrics, get_metrics_tracker
from .scorer import AutoScorer

# Backward-compat MetricsReporter
class MetricsReporter:
    """指标报告器——按维度聚合生成报告。"""
    def __init__(self, tracker: MetricsTracker):
        self._tracker = tracker

    def run_summary(self) -> dict:
        return self._tracker.summary(last_n=1)

    def aggregate_all(self) -> dict:
        return self._tracker.summary(last_n=100)

    def aggregate_by_agent(self) -> dict:
        summary = self._tracker.summary(last_n=100)
        return summary.get("agent_stats", {})

    def report(self) -> str:
        s = self._tracker.summary(last_n=20)
        lines = ["=" * 50, "  Agent Framework v1.0 — Evaluation Report", "=" * 50]
        lines.append(f"\n  Total Runs: {s.get('total_runs', 0)}")
        lines.append(f"  Success Rate: {s.get('success_rate', 0)}%")
        lines.append(f"  Avg Latency: {s.get('avg_latency_s', 0)}s")
        lines.append(f"  Total Tokens: {s.get('total_tokens', 0):,}")
        lines.append(f"  Tool Calls: {s.get('tool_calls', 0)}")
        if s.get('avg_scores'):
            sc = s['avg_scores']
            lines.append(f"  Scores: C{sc.get('correctness',0)}/E{sc.get('efficiency',0)}/T{sc.get('tone',0)}")
        lines.append("=" * 50)
        return "\n".join(lines)
