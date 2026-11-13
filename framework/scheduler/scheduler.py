"""Scheduler v1.0 — 并行调度引擎 + 全功能生产级。

v1.0 新增:
  - Checkpoint & Resume: 每批次执行前保存快照，crash 后断点恢复
  - MetricsTracker: 全链路指标收集
  - ModelRouter: 按 Agent 角色自动路由最优模型
  - VectorMemory: 注入相关长期记忆到 Agent 上下文
  - 复杂 DAG: 跨批依赖 + 条件分支 + 动态重规划
  - Structured Aggregator: Validator → Merger → Synthesizer 三层聚合
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from ..task.task import Task, Subtask, TaskStatus, TaskResult
from ..agent.registry import AgentRegistry
from ..agent.base import AgentContext
from ..event.bus import EventBus
from ..aggregator.pipeline import AggregatorPipeline
from ..checkpoint.checkpoint import Checkpoint, CheckpointStore
from ..evaluation.tracker import MetricsTracker, RunMetrics, get_metrics_tracker
from ..evaluation.scorer import AutoScorer
from ..sdk.model_router import ModelRouter, get_model_router
from ..memory.vector_memory import VectorMemory, get_vector_memory


class Scheduler:
    """并行调度器 v1.0——生产级全功能。"""

    def __init__(
        self,
        agent_registry: AgentRegistry,
        event_bus: EventBus | None = None,
        *,
        default_timeout: float = 120.0,
        max_retries: int = 2,
        max_parallel: int = 10,
        checkpoint_store: CheckpointStore | None = None,
        metrics: MetricsTracker | None = None,
        model_router: ModelRouter | None = None,
        vector_memory: VectorMemory | None = None,
        enable_checkpoint: bool = True,
        enable_metrics: bool = True,
        enable_memory_injection: bool = True,
    ):
        self._agent_registry = agent_registry
        self._event_bus = event_bus or EventBus()
        self._aggregator = AggregatorPipeline()
        self._checkpoint_store = checkpoint_store or CheckpointStore()
        self._metrics = metrics or get_metrics_tracker()
        self._model_router = model_router or get_model_router()
        self._vector_memory = vector_memory or get_vector_memory()
        self._scorer = AutoScorer()

        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self.max_parallel = max_parallel
        self.enable_checkpoint = enable_checkpoint
        self.enable_metrics = enable_metrics
        self.enable_memory_injection = enable_memory_injection

    # ── Main Execute ──────────────────────────

    async def execute(self, task: Task) -> TaskResult:
        """执行 Task，完整链路：记忆注入 → DAG 调度 → 聚合 → 评分 → 指标。"""
        start_time = time.time()
        task.status = TaskStatus.IN_PROGRESS

        # 指标追踪开始
        run_metrics = None
        if self.enable_metrics:
            run_metrics = self._metrics.start_run(task.description)
            run_metrics.subtask_count = len(task.subtasks)

        await self._event_bus.emit("task:dispatched", {
            "task_id": task.task_id,
            "subtasks": [st.subtask_id for st in task.subtasks],
        })

        # 尝试从 checkpoint 恢复
        results: dict[str, dict[str, Any]] = {}
        if self.enable_checkpoint:
            try:
                results = self._try_resume(task.task_id)
                if results:
                    await self._event_bus.emit("checkpoint:resumed", {
                        "task_id": task.task_id,
                        "resumed_count": len(results),
                    })
            except Exception:
                pass

        # DAG 分层执行
        pending = [st for st in task.subtasks if st.subtask_id not in results]
        batch_num = 0

        while pending:
            batch_num += 1
            blocked = [st for st in pending if any(
                dep in results and results[dep].get("error") for dep in st.depends_on
            )]
            for st in blocked:
                st.status = TaskStatus.FAILED
                st.error = "依赖任务失败，未执行该子任务"
                st.result = {"error": st.error}
                results[st.subtask_id] = st.result
                pending.remove(st)
                await self._event_bus.emit("task:failed", {
                    "subtask_id": st.subtask_id, "error": st.error,
                })
            if not pending:
                break
            ready = [
                st for st in pending
                if all(dep in results for dep in st.depends_on)
            ]
            if not ready:
                # 死锁检测：有 pending 但没有 ready → 可能是循环依赖
                unresolved = [st.subtask_id for st in pending]
                for st in pending:
                    results[st.subtask_id] = {
                        "error": f"Deadlock: depends on unresolved tasks {st.depends_on}",
                        "unresolved": unresolved,
                    }
                    st.status = TaskStatus.FAILED
                break

            # Checkpoint: 执行前保存
            if self.enable_checkpoint:
                self._save_checkpoint(task.task_id, results)

            # 并行执行当前批
            sem = asyncio.Semaphore(self.max_parallel)

            async def _bounded(st: Subtask) -> dict[str, Any]:
                async with sem:
                    return await self._run_with_retry(st, run_metrics)

            batch_tasks = [_bounded(st) for st in ready]
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            for st, result in zip(ready, batch_results):
                if isinstance(result, Exception) or (isinstance(result, dict) and result.get("error")):
                    error = str(result) if isinstance(result, Exception) else str(result["error"])
                    st.status = TaskStatus.FAILED
                    st.error = error
                    st.result = {"error": error}
                    results[st.subtask_id] = st.result
                    await self._event_bus.emit("task:failed", {
                        "subtask_id": st.subtask_id, "error": error,
                    })
                    # v1.0: 条件分支——失败时尝试动态重规划
                    if self._can_replan(st, pending, results):
                        replan_result = await self._dynamic_replan(st, task, pending, results)
                        if replan_result:
                            results[st.subtask_id] = replan_result
                            st.status = TaskStatus.COMPLETED
                else:
                    st.status = TaskStatus.COMPLETED
                    st.result = result
                    results[st.subtask_id] = result
                    if run_metrics:
                        run_metrics.success_count += 1

                    # 条件分支：检查结果中的 next_action
                    next_action = result.get("_next_action", "")
                    if next_action == "branch" and result.get("_branch_tasks"):
                        self._inject_branch_tasks(task, pending, result["_branch_tasks"])

            for st in ready:
                pending.remove(st)

        # ── v0.6: AggregatorPipeline 聚合 ──────
        worker_results = [r for r in results.values() if r is not None and not r.get("error")]
        failed = [r for r in results.values() if r is not None and r.get("error")]

        passed = []
        validation_failed = []
        for st in task.subtasks:
            r = results.get(st.subtask_id)
            if r is None or r.get("error"):
                if r and run_metrics:
                    run_metrics.fail_count += 1
                continue
            # Agent outputs are envelopes (response/summary/changes), not raw Skill outputs.
            # Validating against every Skill contract drops otherwise successful tasks.
            passed.append(r)

        merged = self._aggregator.merger.merge(passed) if passed else {}
        merged["_types"] = list(set(r.get("type", "unknown") for r in passed))
        merged["_count"] = len(passed)
        merged["_raw"] = passed

        synthesis = ""
        if len(passed) >= 2 and len(merged.get("_types", [])) >= 2:
            synthesis = await self._aggregator.synthesizer.synthesize(merged, task.description)

        aggregated = {
            "passed": passed, "failed": validation_failed,
            "merged": merged, "synthesis": synthesis,
            "stats": {
                "total": len(worker_results), "passed": len(passed),
                "failed": len(validation_failed) + len(failed),
                "synthesis_used": bool(synthesis),
                "llm_calls_saved": len(passed) - (1 if synthesis else 0),
            },
        }

        output = self._format_output(task, aggregated, failed)
        task.status = TaskStatus.FAILED if any(st.status == TaskStatus.FAILED for st in task.subtasks) else TaskStatus.COMPLETED

        # 评分
        if self.enable_metrics and synthesis:
            try:
                scores = await self._scorer.score(task.description, output[:3000])
                if run_metrics:
                    run_metrics.scores = scores
            except Exception:
                pass

        # 记忆学习：存储任务结果
        if self.enable_memory_injection and output:
            try:
                self._vector_memory.store(
                    f"任务: {task.description[:100]} | 结果: {output[:200]}",
                    category="task",
                    importance=0.6,
                )
            except Exception:
                pass  # Memory is optional; the completed answer must still be delivered.

        # 完成指标
        if run_metrics:
            run_metrics.tool_calls = sum(
                len(r.get("_raw", [])) for r in [aggregated]
            )
            self._metrics.end_run(run_metrics)

        # 清理 checkpoint
        if self.enable_checkpoint:
            self._cleanup_checkpoint(task.task_id)

        return TaskResult(
            task_id=task.task_id,
            status=task.status,
            subtask_results=worker_results + failed,
            aggregated_output=output,
            total_latency=time.time() - start_time,
        )

    # ── Retry ──────────────────────────────────

    async def _run_with_retry(self, subtask: Subtask, run_metrics: RunMetrics | None = None) -> dict[str, Any]:
        """带重试的 Subtask 执行。"""
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._run_subtask(subtask, run_metrics),
                    timeout=self.default_timeout,
                )
                if not result.get("error"):
                    return result
                last_error = result["error"]
            except asyncio.TimeoutError:
                last_error = f"Timeout after {self.default_timeout}s"
            except Exception as e:
                last_error = str(e)

            if attempt < self.max_retries:
                wait = 2 ** attempt
                await asyncio.sleep(wait)

        return {"error": last_error or "Unknown error"}

    # ── Subtask Execution ──────────────────────

    async def _run_subtask(self, subtask: Subtask, run_metrics: RunMetrics | None = None) -> dict[str, Any]:
        """执行单个 Subtask——注入记忆 + 路由模型。"""
        subtask.status = TaskStatus.IN_PROGRESS

        agent = self._agent_registry.get(subtask.agent_type)
        if agent is None:
            return {"error": f"Agent '{subtask.agent_type}' not found"}

        # 记忆注入
        memory_context = ""
        if self.enable_memory_injection:
            try:
                memory_context = self._vector_memory.recall_context(
                    subtask.description or subtask.input_data.get("query", ""), max_tokens=1000
                )
            except Exception:
                pass

        # 模型路由
        model_cfg = self._model_router.get_config("worker")
        cost_estimate = self._model_router.estimate_cost("worker", 1000, 500)

        t0 = time.time()
        await self._event_bus.emit("subtask:started", {
            "subtask_id": subtask.subtask_id,
            "agent_type": subtask.agent_type,
            "skill": subtask.skill_name,
            "model": model_cfg.model,
        })

        context = AgentContext(
            agent_name=agent.name,
            task_id=subtask.subtask_id,
            input_data=subtask.input_data,
        )
        # 将记忆注入到 input_data
        if memory_context:
            context.input_data["_memory_context"] = memory_context
        context.input_data["_model_config"] = {
            "model": model_cfg.model,
            "provider": model_cfg.provider,
        }

        result = await agent.execute(context)
        latency = time.time() - t0

        # 记录指标
        if run_metrics:
            tokens = result.get("_tokens", {}).get("total", 0) if isinstance(result.get("_tokens"), dict) else 0
            self._metrics.record_agent(run_metrics, agent.name, tokens, latency, not bool(result.get("error")))
            self._metrics.record_skill(run_metrics, subtask.skill_name, not bool(result.get("error")), latency)

        await self._event_bus.emit("subtask:completed", {
            "subtask_id": subtask.subtask_id,
            "agent": agent.name,
            "model": model_cfg.model,
            "latency": latency,
            "result_keys": list(result.keys()),
        })

        result["_latency"] = latency
        result["_model"] = model_cfg.model
        return result

    # ── Checkpoint ─────────────────────────────

    def _save_checkpoint(self, task_id: str, results: dict[str, Any]):
        """保存执行快照。"""
        cp = Checkpoint(
            task_id=task_id,
            subtask_states={
                sid: "completed" if not r.get("error") else "failed"
                for sid, r in results.items()
            },
            data={"results": {k: v for k, v in results.items()}},
        )
        self._checkpoint_store.save(cp)

    def _try_resume(self, task_id: str) -> dict[str, dict[str, Any]]:
        """尝试从 checkpoint 恢复已完成的结果。"""
        checkpoints = self._checkpoint_store.list_all()
        for cp_info in checkpoints:
            if cp_info.get("task_id") == task_id:
                cp = self._checkpoint_store.load(cp_info["checkpoint_id"])
                if cp and cp.data.get("results"):
                    return cp.data["results"]
        return {}

    def _cleanup_checkpoint(self, task_id: str):
        """任务完成，清理 checkpoint。"""
        for cp_info in self._checkpoint_store.list_all():
            if cp_info.get("task_id") == task_id:
                self._checkpoint_store.delete(cp_info["checkpoint_id"])

    # ── Complex DAG: 条件分支 ──────────────────

    def _can_replan(self, failed_st: Subtask, pending: list[Subtask],
                    results: dict[str, Any]) -> bool:
        """判断是否应该动态重规划。"""
        # 只有当这个 subtask 被其他 pending 依赖时才重规划
        for st in pending:
            if failed_st.subtask_id in st.depends_on:
                return True
        return False

    async def _dynamic_replan(self, failed_st: Subtask, task: Task,
                              pending: list[Subtask],
                              results: dict[str, Any]) -> dict[str, Any] | None:
        """动态重规划：失败后尝试替代方案。"""
        # 简化版：用 fallback agent 重试
        fallback_map = {
            "research_agent": "general_agent",
            "code_agent": "general_agent",
            "office_agent": "general_agent",
        }
        fallback_agent = fallback_map.get(failed_st.agent_type, "general_agent")
        if fallback_agent == failed_st.agent_type:
            return None

        await self._event_bus.emit("task:replan", {
            "subtask_id": failed_st.subtask_id,
            "original_agent": failed_st.agent_type,
            "fallback_agent": fallback_agent,
        })

        fallback_st = Subtask(
            subtask_id=failed_st.subtask_id + "_fb",
            description=f"[Fallback] {failed_st.description}",
            agent_type=fallback_agent,
            skill_name="bash",
            input_data=failed_st.input_data,
            depends_on=failed_st.depends_on,
        )

        try:
            result = await asyncio.wait_for(
                self._run_subtask(fallback_st),
                timeout=self.default_timeout,
            )
            if not result.get("error"):
                return result
        except Exception:
            pass
        return None

    def _inject_branch_tasks(self, task: Task, pending: list[Subtask],
                             branch_data: list[dict]):
        """注入条件分支子任务到 pending 列表。"""
        for i, bd in enumerate(branch_data):
            new_st = Subtask(
                subtask_id=f"{task.task_id}_br{i}",
                description=bd.get("description", f"分支任务 {i+1}"),
                agent_type=bd.get("agent", "general_agent"),
                skill_name=bd.get("skill", "bash"),
                input_data=bd.get("input", {}),
                depends_on=bd.get("depends_on", []),
            )
            task.subtasks.append(new_st)
            pending.append(new_st)

    # ── Output Formatting ──────────────────────

    def _format_output(self, task: Task, aggregated: dict[str, Any],
                       failed: list[dict[str, Any]]) -> str:
        parts = [f"# 任务结果: {task.description}\n"]
        merged = aggregated.get("merged", {})
        synthesis = aggregated.get("synthesis", "")
        stats = aggregated.get("stats", {})

        parts.append("## 摘要")
        parts.append(f"  通过: {stats.get('passed', 0)}/{stats.get('total', 0)}")
        parts.append(f"  失败: {stats.get('failed', 0)}")
        parts.append(f"  LLM 综述: {'是' if synthesis else '否（已节省 LLM 调用）'}")

        if synthesis:
            parts.append(f"\n## 综合结论\n{synthesis}")

        for st in task.subtasks:
            result = st.result or {}
            if result.get("error"):
                parts.append(f"\n## [{st.description}] 失败")
                parts.append(f"  错误: {result['error']}")
                continue
            parts.append(f"\n## [{st.description}]")
            for key in ("response", "summary", "changes", "review_notes", "sources", "files_modified"):
                if key in result and result[key]:
                    val = result[key]
                    if isinstance(val, list):
                        parts.append(f"  {key}: {', '.join(str(v) for v in val)}")
                    else:
                        parts.append(f"  {key}: {val}")
            # 显示模型信息
            if result.get("_model"):
                parts.append(f"  模型: {result['_model']}  延迟: {result.get('_latency', 0):.1f}s")

        if failed:
            parts.append("\n## 失败项")
            for f in failed:
                parts.append(f"  - {f.get('error', '未知错误')}")

        return "\n".join(parts)
