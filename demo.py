"""Agent Framework v1.0 — Full Pipeline Demo

支持多后端切换:
  --backend anthropic  → ClaudeAdapter (Anthropic API)
  --backend openai     → OpenAIAdapter (DeepSeek / 本地代理)

环境变量:
   Anthropic:  ANTHROPIC_API_KEY
   DeepSeek:   DEEPSEEK_API_KEY  或  OPENAI_API_KEY
   本地代理:    OPENAI_BASE_URL=http://localhost:8000/v1

链路:
  User Input
    → Orchestrator Agent (计划 + Agent 选择, Option A)
    → Scheduler (并行 + 重试 + 超时 + Rate Limiting)
    → Worker Agents (Claude/DeepSeek SDK, 多模型路由)
    → AggregatorPipeline (Validator → Merger → Synthesizer)
    → Evaluation (MetricsTracker + Reporter)
    → Checkpoint + EventLog + Long-term Memory
"""

import asyncio
import os
import sys
sys.path.insert(0, ".")

from framework.workspace import Workspace
from framework.skill import SearchSkill, CodeSkill
from framework.agent import ResearchAgent, CodeAgent
from framework.task import Planner
from framework.scheduler import Scheduler
from framework.sdk.adapter import ClaudeAdapter
from framework.sdk.openai_adapter import OpenAIAdapter
from framework.orchestrator import OrchestratorAgent
from framework.evaluation import MetricsTracker, MetricsReporter


def create_adapter(backend: str = "auto") -> tuple[object, dict]:
    """创建适配器——自动检测可用后端。

    Priority: 显式指定 > DEEPSEEK_API_KEY 环境变量 > ANTHROPIC_API_KEY 环境变量 > Echo
    """
    info: dict = {}

    if backend == "openai" or (backend == "auto" and (
        os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    )):
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        adapter = OpenAIAdapter(
            model="deepseek-chat",
            base_url=base_url,
            rate_limit=5.0,
            rate_burst=10,
        )
        adapter.set_model_for_role("orchestrator", "deepseek-chat")
        adapter.set_model_for_role("research", "deepseek-chat")
        adapter.set_model_for_role("code", "deepseek-chat")
        info = {"backend": "openai", "provider": adapter.backend, "base_url": base_url}
    elif backend in ("anthropic", "auto"):
        adapter = ClaudeAdapter(
            model="claude-sonnet-4-6",
            rate_limit=5.0,
            rate_burst=10,
        )
        adapter.set_model_for_role("orchestrator", "claude-opus-4-7")
        adapter.set_model_for_role("research", "claude-sonnet-4-6")
        adapter.set_model_for_role("code", "claude-sonnet-4-6")
        info = {"backend": "anthropic", "provider": "anthropic"}

    return adapter, info


async def build_workspace(backend: str = "auto") -> tuple[Workspace, MetricsTracker, dict]:
    """装配 Workspace v1.0。"""
    adapter, info = create_adapter(backend)

    ws = Workspace(
        name="demo-v1.0",
        persist_dir=".workspace_data",
    )
    tracker = MetricsTracker()

    # Skill 注册
    ws.skills.register(SearchSkill())
    ws.skills.register(CodeSkill())

    # Agent 注册（共享同一个 adapter）
    ws.agents.register(ResearchAgent(adapter))
    ws.agents.register(CodeAgent(adapter))

    # Orchestrator + Planner
    orchestrator = OrchestratorAgent(adapter)
    planner = Planner(ws.agents, ws.skills, orchestrator)

    # Scheduler
    scheduler = Scheduler(
        ws.agents,
        ws.event_bus,
        default_timeout=120.0,
        max_retries=2,
        max_parallel=10,
    )

    ws._planner = planner
    ws._scheduler = scheduler
    ws._tracker = tracker
    ws._adapter = adapter

    return ws, tracker, info


async def run_demo(ws: Workspace, tracker: MetricsTracker, info: dict, user_input: str):
    planner = ws._planner
    scheduler = ws._scheduler
    adapter = ws._adapter

    print("=" * 60)
    print(f"  Agent Framework v1.0 — Full Pipeline")
    print(f"  Backend: {info.get('backend', 'unknown')} | Provider: {info.get('provider', 'unknown')}")
    print(f"  Mode: {adapter.mode} | Rate Limit: 5 req/s")
    if info.get("base_url"):
        print(f"  Endpoint: {info['base_url']}")
    print("=" * 60)
    print(f"\n  Input: {user_input}\n")

    # ── Step 1: Orchestrator ────────────────
    print("  [Orchestrator] Planning...")
    task = await planner.plan(user_input)
    print(f"  [Orchestrator] → {len(task.subtasks)} subtask(s):")
    for st in task.subtasks:
        deps = f" (depends on {st.depends_on})" if st.depends_on else ""
        print(f"    - [{st.subtask_id}] {st.agent_type}/{st.skill_name}: {st.description[:60]}{deps}")
    print()

    # ── Step 2: Submit → Workspace ──────────
    await ws.submit_task(task)
    await ws.record_decision(
        context=f"Planning for: {user_input[:80]}",
        decision=f"Orchestrator created {len(task.subtasks)} subtasks",
        reason="Analysis of user intent and agent capabilities",
    )

    # ── Step 3: Scheduler ───────────────────
    tracker.start_run(task.task_id, task.description)
    print(f"  [Scheduler] Executing (retries={scheduler.max_retries}, timeout={scheduler.default_timeout}s)...\n")
    result = await scheduler.execute(task)

    # ── Step 4: Evaluation ──────────────────
    for st in task.subtasks:
        usage = st.result.get("usage", {}) if st.result else {}
        tracker.record_subtask(
            subtask_id=st.subtask_id,
            agent=st.agent_type,
            skill=st.skill_name,
            usage=usage,
            latency=result.total_latency,
            success=st.status.value == "completed",
            error=st.error or "",
        )
    tracker.finish_run(result.status.value, adapter.mode)

    # ── Step 5: Checkpoint ──────────────────
    cp = await ws.save_checkpoint(task)
    print(f"  [Checkpoint] saved: {cp.checkpoint_id} (v{cp.version}, gzip)\n")

    # ── Step 6: Output ──────────────────────
    print(f"  [Result]\n{result.aggregated_output}\n")
    print(f"  Latency: {result.total_latency:.2f}s | Status: {result.status.value}")

    # ── Step 7: Evaluation Report ───────────
    reporter = MetricsReporter(tracker)
    print(f"\n{'─' * 60}")
    print(reporter.report())

    # ── Step 8: Event Stats ─────────────────
    print(f"\n{'─' * 60}")
    print(f"  [Event Stats]")
    for evt, count in ws.event_counts().items():
        print(f"    {evt}: {count}")

    # ── Step 9: Workspace Snapshot ──────────
    print(f"\n{'─' * 60}")
    print(f"  [Workspace Snapshot]")
    for k, v in ws.snapshot().items():
        print(f"    {k}: {v}")

    # ── Step 10: Resume ─────────────────────
    restored = await ws.resume(cp.checkpoint_id)
    assert restored is not None
    print(f"\n  [Resume] checkpoint restored: {restored.checkpoint_id} (v{restored.version}) ✓")

    # ── Step 11: Skill Registry ─────────────
    print(f"\n{'─' * 60}")
    stats = ws.skills.get_stats()
    print(f"  [Skill Registry v0.7] {stats['skill_count']} skills, {stats['capability_count']} capabilities")
    for name, info in stats["skills"].items():
        print(f"    {name}: cost={info['scores']['cost']} lat={info['scores']['latency']} qual={info['scores']['quality']}")

    # ── Step 12: Memory ─────────────────────
    ws.memory.set_long_term(f"task:{task.task_id}", task.description)
    mem_snap = ws.memory.snapshot()
    print(f"\n  [Memory v1.0] short-term={mem_snap['short_term_size']}, long-term={mem_snap['long_term_size']} keys")

    ws.save()

    print(f"\n{'=' * 60}")
    print(f"  v1.0 Full Pipeline Verified ({info.get('provider', 'echo').upper()}).")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    user_input = sys.argv[1] if len(sys.argv) > 1 else (
        "搜索 Python asyncio 最佳实践，并重构 auth 模块的 JWT 实现"
    )
    backend = sys.argv[2] if len(sys.argv) > 2 else "auto"

    ws, tracker, info = asyncio.run(build_workspace(backend))
    asyncio.run(run_demo(ws, tracker, info, user_input))
