"""MetricsTracker v0.9 — 全链路指标收集 + 汇总报告 + 趋势分析。

追踪维度:
  - 任务级别: 成功率、延迟、Token、成本
  - Agent 级别: 各 Agent 独立指标
  - Skill 级别: 工具调用频率和成功率
  - 时间维度: 趋势变化
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunMetrics:
    """单次运行指标。"""
    run_id: str = ""
    task_description: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    total_latency: float = 0.0
    subtask_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    estimated_cost_usd: float = 0.0
    # 评分
    scores: dict[str, float] = field(default_factory=dict)
    # 分解
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    skill_metrics: dict[str, dict] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.subtask_count == 0:
            return 0.0
        return self.success_count / self.subtask_count

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_description": self.task_description,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_latency": self.total_latency,
            "subtask_count": self.subtask_count,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "total_tokens": self.total_tokens,
            "tool_calls": self.tool_calls,
            "estimated_cost_usd": self.estimated_cost_usd,
            "scores": self.scores,
            "agent_metrics": self.agent_metrics,
            "skill_metrics": self.skill_metrics,
        }


class MetricsTracker:
    """全链路指标收集器——持久化 + 聚合 + 趋势。"""

    def __init__(self, store_path: str = ".metrics/metrics.jsonl"):
        self._path = Path(store_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._runs: list[RunMetrics] = []
        self._load()

    # ── Recording ────────────────────────────

    def start_run(self, task_description: str) -> RunMetrics:
        """开始一次运行追踪。"""
        import uuid
        run = RunMetrics(
            run_id=uuid.uuid4().hex[:8],
            task_description=task_description,
            start_time=time.time(),
        )
        self._runs.append(run)
        return run

    def end_run(self, run: RunMetrics):
        """结束一次运行追踪并持久化。"""
        run.end_time = time.time()
        if run.start_time:
            run.total_latency = run.end_time - run.start_time
        self._append_jsonl(run.to_dict())

    def record_agent(self, run: RunMetrics, agent_name: str, tokens: int, latency: float, success: bool):
        """记录单个 Agent 的指标。"""
        if agent_name not in run.agent_metrics:
            run.agent_metrics[agent_name] = {"calls": 0, "tokens": 0, "latency": 0, "success": 0, "fail": 0}
        m = run.agent_metrics[agent_name]
        m["calls"] += 1
        m["tokens"] += tokens
        m["latency"] += latency
        if success:
            m["success"] += 1
        else:
            m["fail"] += 1

    def record_skill(self, run: RunMetrics, skill_name: str, success: bool, latency: float):
        """记录单个 Skill 的调用。"""
        if skill_name not in run.skill_metrics:
            run.skill_metrics[skill_name] = {"calls": 0, "success": 0, "fail": 0, "total_latency": 0.0}
        m = run.skill_metrics[skill_name]
        m["calls"] += 1
        m["total_latency"] += latency
        if success:
            m["success"] += 1
        else:
            m["fail"] += 1

    def record_tokens(self, run: RunMetrics, input_tokens: int, output_tokens: int):
        run.input_tokens += input_tokens
        run.output_tokens += output_tokens
        run.total_tokens += input_tokens + output_tokens

    # ── Reports ──────────────────────────────

    def summary(self, last_n: int = 50) -> dict[str, Any]:
        """最近 N 次运行的汇总报告。"""
        runs = self._runs[-last_n:]
        if not runs:
            return {"total_runs": 0, "message": "暂无数据"}

        total = len(runs)
        success_runs = sum(1 for r in runs if r.fail_count == 0)
        avg_latency = sum(r.total_latency for r in runs) / total
        total_tokens = sum(r.total_tokens for r in runs)
        total_cost = sum(r.estimated_cost_usd for r in runs)
        tool_calls = sum(r.tool_calls for r in runs)

        # Agent 聚合
        agent_stats: dict = defaultdict(lambda: {"calls": 0, "success": 0, "fail": 0})
        for r in runs:
            for name, m in r.agent_metrics.items():
                agent_stats[name]["calls"] += m["calls"]
                agent_stats[name]["success"] += m["success"]
                agent_stats[name]["fail"] += m["fail"]

        # Skill 聚合
        skill_stats: dict = defaultdict(lambda: {"calls": 0, "success": 0, "fail": 0})
        for r in runs:
            for name, m in r.skill_metrics.items():
                skill_stats[name]["calls"] += m["calls"]
                skill_stats[name]["success"] += m["success"]
                skill_stats[name]["fail"] += m["fail"]

        # 平均评分
        avg_scores = {"correctness": 0, "completeness": 0, "efficiency": 0, "tone": 0}
        score_count = 0
        for r in runs:
            if r.scores:
                for k in avg_scores:
                    avg_scores[k] += r.scores.get(k, 0)
                score_count += 1
        if score_count > 0:
            for k in avg_scores:
                avg_scores[k] = round(avg_scores[k] / score_count, 1)

        return {
            "total_runs": total,
            "success_rate": round(success_runs / total * 100, 1),
            "avg_latency_s": round(avg_latency, 2),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 4),
            "tool_calls": tool_calls,
            "avg_scores": avg_scores,
            "agent_stats": dict(agent_stats),
            "skill_stats": dict(skill_stats),
        }

    def trend(self, metric: str = "latency", last_n: int = 20) -> list[dict]:
        """指标趋势（用于前端折线图）。"""
        runs = self._runs[-last_n:]
        trend_data = []
        for r in runs:
            point = {"time": r.start_time, "run_id": r.run_id}
            if metric == "latency":
                point["value"] = r.total_latency
            elif metric == "tokens":
                point["value"] = r.total_tokens
            elif metric == "cost":
                point["value"] = r.estimated_cost_usd
            elif metric == "success_rate":
                point["value"] = r.success_rate
            trend_data.append(point)
        return trend_data

    def agent_report(self, agent_name: str, last_n: int = 20) -> dict:
        """单 Agent 详细报告。"""
        runs = self._runs[-last_n:]
        calls = 0; success = 0; fail = 0; tokens = 0; latency = 0.0
        for r in runs:
            m = r.agent_metrics.get(agent_name)
            if m:
                calls += m["calls"]
                success += m["success"]
                fail += m["fail"]
                tokens += m["tokens"]
                latency += m["latency"]
        return {
            "agent": agent_name,
            "total_calls": calls,
            "success": success,
            "fail": fail,
            "success_rate": round(success / calls * 100, 1) if calls else 0,
            "total_tokens": tokens,
            "avg_latency_s": round(latency / calls, 2) if calls else 0,
        }

    # ── Persistence ──────────────────────────

    def _append_jsonl(self, data: dict):
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _load(self):
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        run = RunMetrics(
                            run_id=d.get("run_id", ""),
                            task_description=d.get("task_description", ""),
                            start_time=d.get("start_time", 0),
                            end_time=d.get("end_time", 0),
                            total_latency=d.get("total_latency", 0),
                            subtask_count=d.get("subtask_count", 0),
                            success_count=d.get("success_count", 0),
                            fail_count=d.get("fail_count", 0),
                            total_tokens=d.get("total_tokens", 0),
                            tool_calls=d.get("tool_calls", 0),
                            estimated_cost_usd=d.get("estimated_cost_usd", 0),
                            scores=d.get("scores", {}),
                            agent_metrics=d.get("agent_metrics", {}),
                            skill_metrics=d.get("skill_metrics", {}),
                        )
                        self._runs.append(run)
                    except Exception:
                        continue
        except Exception:
            pass

    @property
    def run_count(self) -> int:
        return len(self._runs)


# 全局单例
_tracker: MetricsTracker | None = None

def get_metrics_tracker() -> MetricsTracker:
    global _tracker
    if _tracker is None:
        _tracker = MetricsTracker()
    return _tracker
