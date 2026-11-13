"""Agent Framework — 对话式交互终端

用法:
  python chat.py                  # DeepSeek (自动检测)
  python chat.py --backend anthropic  # Anthropic
  python chat.py --backend openai     # DeepSeek / 代理

命令:
  /help      — 帮助
  /history   — 查看任务历史
  /stats     — 查看统计
  /replay    — 重放上次执行事件
  /clear     — 清屏
  /exit      — 退出
"""

import asyncio
import os
import sys
try:
    import readline  # noqa: Unix line editing
except ImportError:
    try:
        import pyreadline3 as readline  # noqa: Windows fallback
    except ImportError:
        pass  # no line editing, still works

sys.path.insert(0, ".")

from framework.workspace import Workspace
from framework.skill import SearchSkill, CodeSkill
from framework.skill.claude_tools import (
    BashSkill, ReadSkill, WriteSkill, EditSkill,
    GlobSkill, GrepSkill, WebSearchSkill, WebFetchSkill, TodoWriteSkill,
)
from framework.agent import ResearchAgent, CodeAgent, GeneralAgent
from framework.task import Planner
from framework.scheduler import Scheduler
from framework.sdk.adapter import ClaudeAdapter
from framework.sdk.openai_adapter import OpenAIAdapter
from framework.orchestrator import OrchestratorAgent
from framework.evaluation import MetricsTracker, MetricsReporter


# ─── 颜色 ──────────────────────────────────────

class C:
    R = "\033[91m"
    G = "\033[92m"
    Y = "\033[93m"
    B = "\033[94m"
    M = "\033[95m"
    C = "\033[96m"
    W = "\033[97m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RST = "\033[0m"


def print_banner():
    banner = f"""
{C.BOLD}{C.M}  ╔══════════════════════════════════════╗
  ║     Agent Framework v1.0              ║
  ║     Workspace OS 级编排层              ║
  ╚══════════════════════════════════════╝{C.RST}
"""
    print(banner)


# ─── 装配 ──────────────────────────────────────

def create_adapter(backend: str = "auto"):
    info = {}
    ds_key = os.environ.get("DEEPSEEK_API_KEY")
    oai_key = os.environ.get("OPENAI_API_KEY")
    anth_key = os.environ.get("ANTHROPIC_API_KEY")

    if backend == "openai" or (backend == "auto" and (ds_key or oai_key)):
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        adapter = OpenAIAdapter(model="deepseek-chat", base_url=base_url)
        adapter.set_model_for_role("orchestrator", "deepseek-chat")
        adapter.set_model_for_role("research", "deepseek-chat")
        adapter.set_model_for_role("code", "deepseek-chat")
        info = {"backend": "openai", "provider": adapter.backend, "base_url": base_url}
    elif backend in ("anthropic", "auto") and anth_key:
        adapter = ClaudeAdapter(model="claude-sonnet-4-6")
        adapter.set_model_for_role("orchestrator", "claude-opus-4-7")
        adapter.set_model_for_role("research", "claude-sonnet-4-6")
        adapter.set_model_for_role("code", "claude-sonnet-4-6")
        info = {"backend": "anthropic", "provider": "anthropic"}
    else:
        # Echo fallback
        adapter = OpenAIAdapter(model="deepseek-chat")
        info = {"backend": "echo", "provider": "echo", "base_url": "N/A"}

    return adapter, info


async def build_workspace(backend: str = "auto"):
    adapter, info = create_adapter(backend)
    ws = Workspace(name="chat", persist_dir=".workspace_data")
    tracker = MetricsTracker()

    ws.skills.register(SearchSkill())
    ws.skills.register(CodeSkill())
    # Claude Code 全套工具
    ws.skills.register(BashSkill()); ws.skills.register(ReadSkill())
    ws.skills.register(WriteSkill()); ws.skills.register(EditSkill())
    ws.skills.register(GlobSkill()); ws.skills.register(GrepSkill())
    ws.skills.register(WebSearchSkill()); ws.skills.register(WebFetchSkill())
    ws.skills.register(TodoWriteSkill())
    # Agent 注册
    ws.agents.register(ResearchAgent(adapter))
    ws.agents.register(CodeAgent(adapter))
    ws.agents.register(GeneralAgent(adapter))

    orchestrator = OrchestratorAgent(adapter)
    planner = Planner(ws.agents, ws.skills, orchestrator)
    scheduler = Scheduler(ws.agents, ws.event_bus, default_timeout=180.0, max_retries=1)

    ws._planner = planner
    ws._scheduler = scheduler
    ws._tracker = tracker
    ws._adapter = adapter
    return ws, tracker, info


# ─── 执行 ──────────────────────────────────────

async def process(ws: Workspace, tracker: MetricsTracker, user_input: str):
    planner = ws._planner
    scheduler = ws._scheduler

    task = await planner.plan(user_input)

    # 打印计划
    print(f"\n{C.C}  Plan:{C.RST} {len(task.subtasks)} subtask(s)")
    for st in task.subtasks:
        deps = f" {C.DIM}(<- {', '.join(st.depends_on)}){C.RST}" if st.depends_on else ""
        print(f"    {C.Y}{st.subtask_id}{C.RST}. {st.agent_type}/{st.skill_name}: {st.description[:70]}{deps}")

    await ws.submit_task(task)
    tracker.start_run(task.task_id, task.description)

    print(f"\n{C.DIM}  Executing...{C.RST}")
    result = await scheduler.execute(task)

    for st in task.subtasks:
        usage = st.result.get("usage", {}) if st.result else {}
        tracker.record_subtask(
            subtask_id=st.subtask_id, agent=st.agent_type, skill=st.skill_name,
            usage=usage, latency=result.total_latency,
            success=st.status.value == "completed", error=st.error or "",
        )
    tracker.finish_run(result.status.value, ws._adapter.mode)

    await ws.save_checkpoint(task)
    reporter = MetricsReporter(tracker)

    # 打印结果
    print(f"\n{C.BOLD}{C.G}  Result:{C.RST} ({result.total_latency:.1f}s | {reporter.run_summary().get('tokens', {}).get('total', 0):,} tokens)\n")

    for st in task.subtasks:
        r = st.result or {}
        if "error" in r:
            print(f"  {C.R}[{st.description}] FAILED: {r['error']}{C.RST}")
            continue
        print(f"  {C.B}[{st.description}]{C.RST}")
        for key, val in r.items():
            if key in ("agent", "task_id", "type", "usage", "mode", "claude_response", "relevance_score"):
                continue
            if isinstance(val, str) and len(val) > 300:
                val = val[:300] + f"{C.DIM}...(truncated){C.RST}"
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            print(f"    {C.DIM}{key}:{C.RST} {val}")
        print()

    ws.save()


# ─── 命令处理 ──────────────────────────────────

def handle_command(cmd: str, ws: Workspace, tracker: MetricsTracker) -> bool:
    """处理 / 命令，返回 True 表示继续，False 表示退出。"""
    parts = cmd.strip().split()
    op = parts[0].lower()

    if op == "/exit" or op == "/quit":
        print(f"\n{C.Y}  Goodbye.{C.RST}")
        return False

    elif op == "/help":
        print(f"""
{C.B}  Commands:{C.RST}
    /help       — 帮助
    /history    — 任务历史
    /stats      — 运行统计
    /replay     — 重放事件
    /mode       — 查看当前模式
    /clear      — 清屏
    /exit       — 退出

{C.B}  Just type to run a task.{C.RST}
""")

    elif op == "/history":
        print(f"\n{C.B}  Task History:{C.RST}")
        for i, m in enumerate(tracker.history[-10:], 1):
            status = f"{C.G}✓{C.RST}" if m.status == "completed" else f"{C.R}failed{C.RST}"
            print(f"    {i}. [{status}] {m.task_description[:80]} ({m.total_latency:.1f}s)")

    elif op == "/stats":
        reporter = MetricsReporter(tracker)
        print(reporter.report())

    elif op == "/replay":
        print(f"\n{C.B}  Event Replay ({len(ws.event_log)} events):{C.RST}")
        for e in ws.event_log.replay()[-20:]:
            ts = e['timestamp']
            import datetime
            t = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            print(f"    {C.DIM}{t}{C.RST}  {C.Y}{e['event']}{C.RST}  {str(e['payload'])[:80]}")

    elif op == "/mode":
        a = ws._adapter
        print(f"\n  Mode: {C.G if a.mode == 'live' else C.Y}{a.mode.upper()}{C.RST}  |  Backend: {getattr(a, 'backend', 'anthropic')}")

    elif op == "/clear":
        os.system("cls" if os.name == "nt" else "clear")
        print_banner()

    return True


# ─── 主循环 ────────────────────────────────────

async def main():
    backend = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--backend" else "auto"
    ws, tracker, info = await build_workspace(backend)
    adapter = ws._adapter

    print_banner()
    print(f"  {C.DIM}Backend:{C.RST} {info.get('provider', '?')}  |  "
          f"{C.DIM}Mode:{C.RST} {C.G if adapter.mode == 'live' else C.Y}{adapter.mode.upper()}{C.RST}  |  "
          f"{C.DIM}Type /help for commands{C.RST}")
    print()

    while True:
        try:
            user_input = input(f"  {C.BOLD}>{C.RST} ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{C.Y}  Goodbye.{C.RST}")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not handle_command(user_input, ws, tracker):
                break
            continue

        try:
            await process(ws, tracker, user_input)
        except Exception as e:
            print(f"\n{C.R}  Error: {e}{C.RST}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
