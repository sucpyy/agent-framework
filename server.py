"""Agent Framework Web UI — FastAPI Server v1.0

全功能集成:
  - 多模型路由 (ModelRouter)
  - Checkpoint & Resume
  - MetricsTracker + 趋势报告
  - VectorMemory 长期记忆
  - 复杂 DAG 调度
  - 策略选择 (optimize)
  - 流式 WebSocket + REST API
"""

import asyncio, json, os, shutil, sys, time, uuid
from pathlib import Path
from contextlib import asynccontextmanager
from contextvars import ContextVar
from weakref import WeakValueDictionary

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from framework.workspace import Workspace
from framework.skill import SearchSkill, CodeSkill
from framework.skill.claude_tools import (
    BashSkill, ReadSkill, WriteSkill, EditSkill,
    GlobSkill, GrepSkill, TodoWriteSkill, SpawnAgentSkill,
)
from framework.skill.web_search import WebSearchSkill
from framework.skill.office_tools import (
    EmailSkill, DocumentSkill, CalendarSkill, TranslateSkill, ClipboardSkill,
)
from framework.agent import ResearchAgent, CodeAgent, GeneralAgent, OfficeAgent
from framework.evaluation.scorer import AutoScorer
from framework.evaluation.tracker import MetricsTracker, RunMetrics, get_metrics_tracker
from framework.session.store import SessionStore
from framework.auth.users import UserManager, Role, User
from framework.settings import get_settings, Settings
from framework.agent.base import AgentContext
from framework.task import Planner
from framework.scheduler import Scheduler
from framework.sdk.openai_adapter import OpenAIAdapter
from framework.sdk.model_router import ModelRouter, get_model_router
from framework.orchestrator import OrchestratorAgent
from framework.checkpoint.checkpoint import CheckpointStore
from framework.memory.vector_memory import VectorMemory, get_vector_memory
from framework.persistence import get_store

# ─── FastAPI App ──────────────────────────────

app = FastAPI(title="Agent Framework v1.0")
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# ─── State ────────────────────────────────────
import time, os
_start_time = 0.0


_adapter: OpenAIAdapter | None = None
_workspace: Workspace | None = None
_tracker: MetricsTracker | None = None
_clients: dict[str, WebSocket] = {}  # cid → ws
_general_agent: GeneralAgent | None = None
_scorer: AutoScorer | None = None
_sessions: SessionStore | None = None
_users: UserManager | None = None
_model_router: ModelRouter | None = None
_vector_memory: VectorMemory | None = None
_checkpoint_store: CheckpointStore | None = None
_session_locks: WeakValueDictionary = WeakValueDictionary()
_active_full_run: ContextVar[str | None] = ContextVar("active_full_run", default=None)


def _get_users():
    global _users
    if _users is None:
        _users = UserManager(".auth")
    return _users


def _get_adapter(force_reload: bool = False):
    global _adapter
    if _adapter is None or force_reload:
        s = get_settings()
        _adapter = OpenAIAdapter(
            model=s.llm_model,
            api_key=s.llm_api_key or os.environ.get("DEEPSEEK_API_KEY"),
            base_url=s.llm_base_url or os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
        )
    return _adapter


def _get_ga():
    global _general_agent
    if _general_agent is None:
        _general_agent = GeneralAgent(_get_adapter())
        ws, _ = _get_ws()
        _general_agent.set_registry(ws.agents)
    return _general_agent

def _get_scorer():
    global _scorer
    if _scorer is None:
        _scorer = AutoScorer(_get_adapter())
    return _scorer

def _get_sessions():
    global _sessions
    if _sessions is None:
        _sessions = SessionStore(".sessions")
    return _sessions


def _load_session(session_id):
    try:
        session = _get_sessions().load(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _append_message(session_id: str, role: str, content: str, tool_calls=None):
    session = _get_sessions().append_message(session_id, role, content, tool_calls)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

def _get_model_router():
    global _model_router
    if _model_router is None:
        _model_router = get_model_router()
    return _model_router

def _get_vector_memory():
    global _vector_memory
    if _vector_memory is None:
        _vector_memory = get_vector_memory()
    return _vector_memory

def _get_checkpoint_store():
    global _checkpoint_store
    if _checkpoint_store is None:
        _checkpoint_store = CheckpointStore()
    return _checkpoint_store


def _get_ws():
    global _workspace, _tracker
    if _workspace is None:
        a = _get_adapter()
        _workspace = Workspace(name="webui", persist_dir=".workspace_data")
        _tracker = get_metrics_tracker()

        # 注册全部 17 个技能
        for sk in [SearchSkill(), CodeSkill(), BashSkill(), ReadSkill(), WriteSkill(),
                    EditSkill(), GlobSkill(), GrepSkill(), WebSearchSkill(), TodoWriteSkill(),
                    SpawnAgentSkill(),
                    EmailSkill(), DocumentSkill(), CalendarSkill(), TranslateSkill(), ClipboardSkill()]:
            _workspace.skills.register(sk)

        # 注册 4 个 Agent——Worker 互不可见，仅 GeneralAgent 持有 registry（快速通道用）
        research = ResearchAgent(a)
        code = CodeAgent(a)
        ga = _get_ga()
        office = OfficeAgent(a)
        ga.set_registry(_workspace.agents)  # 仅 GeneralAgent 可 spawn
        _workspace.agents.register(research)
        _workspace.agents.register(code)
        _workspace.agents.register(ga)
        _workspace.agents.register(office)

        orch = OrchestratorAgent(a)
        _workspace._planner = Planner(_workspace.agents, _workspace.skills, orch)

        # v1.0 Scheduler: 全功能
        _workspace._scheduler = Scheduler(
            _workspace.agents,
            _workspace.event_bus,
            default_timeout=180.0,
            max_retries=1,
            max_parallel=10,
            checkpoint_store=_get_checkpoint_store(),
            metrics=_tracker,
            model_router=_get_model_router(),
            vector_memory=_get_vector_memory(),
            enable_checkpoint=True,
            enable_metrics=True,
            enable_memory_injection=True,
        )
        _workspace._tracker = _tracker
        _workspace._adapter = a
    return _workspace, _tracker


async def _send(ws: WebSocket, event: str, data: dict):
    try:
        await ws.send_text(json.dumps({"event": event, "data": data}, ensure_ascii=False))
    except Exception:
        pass



# ─── Fast Path: GeneralAgent Direct ────────────

_agent_status: dict[str, str] = {}

async def _run_fast(ws: WebSocket, task_id: str, msg: str, session_id: str, history: list[dict]):
    """快速通道：跳过 Orchestrator，直连 GeneralAgent 流式输出。"""
    ga = _get_ga()
    _agent_status["general_agent"] = "working"

    await _send(ws, "status", {"task_id": task_id, "phase": "thinking", "msg": "thinking..."})
    await _send(ws, "plan", {"task_id": task_id, "subtasks": [
        {"id": "1", "description": msg[:80], "agent": "general_agent", "skill": "all", "depends_on": []}
    ]})

    start = time.time()
    total_tokens = {"input_tokens": 0, "output_tokens": 0}
    tool_calls = []
    content_parts = []

    try:
        adapter = _get_adapter()

        # 构建消息列表——包含最近 5 条上下文
        msg_list = []
        for message in history[-5:]:
            content = message.get("content", "")
            if message.get("role") in ("user", "assistant") and isinstance(content, str):
                msg_list.append({"role": message["role"], "content": content[:2000]})
        # 追加当前消息
        msg_list.append({"role": "user", "content": msg})

        tools = ga.get_all_tool_schemas()
        handlers = ga.get_all_tool_handlers()

        async for event in adapter.run_stream(
            system_prompt=ga.system_prompt,
            messages=msg_list,
            tools=tools,
            tool_handlers=handlers,
            role="general",
        ):
            if event["type"] == "text":
                content_parts.append(event["content"])
                await _send(ws, "text", {"task_id": task_id, "content": event["content"]})
            elif event["type"] == "tool_start":
                await _send(ws, "tool_start", {"task_id": task_id, "name": event["name"], "input": event.get("input", {})})
            elif event["type"] == "tool_end":
                await _send(ws, "tool_end", {"task_id": task_id, "name": event["name"], "output": event.get("output", {})})
                tool_calls.append({"name": event["name"], "output": event.get("output", {})})
            elif event["type"] == "done":
                total_tokens = event.get("usage", total_tokens)

    except Exception as e:
        if content_parts:
            _append_message(session_id, "assistant", "".join(content_parts), tool_calls)
        await _send(ws, "error", {"task_id": task_id, "session_id": session_id, "error": str(e)})
        _agent_status["general_agent"] = "idle"
        return

    elapsed = time.time() - start
    tok = total_tokens.get("input_tokens", 0) + total_tokens.get("output_tokens", 0)

    # 学习：存储到长期记忆
    content = "".join(content_parts)
    _append_message(session_id, "assistant", content, tool_calls)
    try:
        _get_vector_memory().store(
            f"用户请求: {msg[:100]} | 工具调用: {len(tool_calls)}次 | 耗时: {elapsed:.1f}s",
            category="task", importance=0.5,
        )
    except Exception:
        pass  # Optional memory must not prevent delivery of a saved answer.

    await _send(ws, "result", {
        "task_id": task_id, "session_id": session_id, "content": content, "latency": elapsed,
        "subtasks": [{"subtask_id": "1", "description": msg[:80],
                       "response": "[streamed]", "tool_calls": tool_calls, "status": "completed"}],
        "summary": {"mode": adapter.mode, "tokens": {"total": tok}},
    })

    # Auto-evaluation
    try:
        scorer = _get_scorer()
        score = await asyncio.wait_for(scorer.score(msg, content), timeout=SCORE_TIMEOUT)
        await _send(ws, "score", {"task_id": task_id, "scores": score})
    except Exception:
        pass

    await _send(ws, "status", {"task_id": task_id, "phase": "done", "msg": "done"})
    _agent_status["general_agent"] = "idle"


# ─── Slow Path: Orchestrator → Scheduler ──────

async def _run_full(ws: WebSocket, task_id: str, msg: str, session_id: str):
    """完整通道：Orchestrator 拆解 → Scheduler 并行调度（含全部 v1.0 功能）。"""
    ws_obj, tracker = _get_ws()
    planner = ws_obj._planner
    scheduler = ws_obj._scheduler

    await _send(ws, "status", {"task_id": task_id, "phase": "planning", "msg": "planning..."})
    task = await planner.plan(msg)

    await _send(ws, "plan", {"task_id": task_id, "subtasks": [
        {"id": st.subtask_id, "description": st.description,
         "agent": st.agent_type, "skill": st.skill_name, "depends_on": st.depends_on}
        for st in task.subtasks
    ]})

    await ws_obj.submit_task(task)
    await _send(ws, "status", {"task_id": task_id, "phase": "executing",
                                "msg": f"running {len(task.subtasks)} subtasks..."})

    # Event hooks → WebSocket
    async def hook(**kw):
        # Planner IDs ("1", "2", ...) repeat across tasks; execution context owns the event.
        if _active_full_run.get() != task_id:
            return
        payload = kw.get("payload", {})
        if payload.get("task_id") != task.task_id and payload.get("subtask_id") not in {
            subtask.subtask_id for subtask in task.subtasks
        }:
            return
        await _send(ws, "event", {"task_id": task_id, "event": kw.get("event", "?"),
                                   "payload": payload})
    events = ("subtask:started", "subtask:completed", "task:failed", "task:replan", "checkpoint:resumed")
    for evt in events:
        ws_obj.event_bus.on(evt, hook)

    # Execute with full v1.0 scheduler
    run_token = _active_full_run.set(task_id)
    try:
        result = await scheduler.execute(task)
    finally:
        _active_full_run.reset(run_token)
        for evt in events:
            ws_obj.event_bus.off(evt, hook)

    # Build subtask results
    subtask_results = []
    for st in task.subtasks:
        r = st.result or {}
        f = {k: v for k, v in r.items() if k not in ("agent", "task_id", "type", "usage", "mode", "claude_response")}
        f["subtask_id"] = st.subtask_id
        f["description"] = st.description
        f["status"] = st.status.value
        f["error"] = st.error
        f["model"] = r.get("_model", "unknown")
        subtask_results.append(f)

    # Summary from metrics
    summary = tracker.summary(last_n=10) if tracker else {}

    _append_message(session_id, "assistant", result.aggregated_output)
    await _send(ws, "result", {
        "task_id": task_id, "session_id": session_id,
        "content": result.aggregated_output, "latency": result.total_latency,
        "subtasks": subtask_results, "summary": summary,
    })

    # Auto-scoring
    try:
        scorer = _get_scorer()
        score = await asyncio.wait_for(
            scorer.score(msg, result.aggregated_output[:3000]), timeout=SCORE_TIMEOUT,
        )
        await _send(ws, "score", {"task_id": task_id, "scores": score})
    except Exception:
        pass

    await _send(ws, "status", {"task_id": task_id, "phase": "done", "msg": "done"})


# ─── Complex Query Detection ──────────────────

def _is_complex(msg: str) -> bool:
    keywords = ["并且", "同时", "然后", "先", "再", "之后", "比较", "对比",
                "搜索并", "查并", "调研并", "and also", "and then",
                "两个", "两个任务", "分别", "并行"]
    if len(msg) < 30:
        return False
    return any(kw in msg.lower() for kw in keywords)


def _needs_office_agent(msg: str) -> bool:
    """Short office requests use the office worker instead of the general fast path."""
    lowered = msg.lower()
    return any(keyword in lowered for keyword in (
        "邮件", "email", "发信", "写信", "文档", "报告", "word", "markdown 文件",
        "日程", "日历", "calendar", "提醒", "翻译", "translate", "译成",
        "剪贴板", "clipboard",
    ))




# ═══════════════════════════════════════════════════
# API Routes
# ═══════════════════════════════════════════════════

_start_time = time.time()
SCORE_TIMEOUT = 5.0

@app.get("/")
async def index():
    return HTMLResponse((Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8"))


# ─── Health Check ──────────────────────────────

@app.get("/health")
async def health_check():
    """返回各模块状态，用于前端监控面板。"""
    ws_ok = False
    agents_status = {}
    skills_count = 0
    memory_ok = False
    router_ok = False

    try:
        ws_obj, _ = _get_ws()
        ws_ok = ws_obj is not None
        for name in ws_obj.agents.list_all():
            agents_status[name] = _agent_status.get(name, "idle")
        skills_count = len(ws_obj.skills._skills)
    except Exception:
        pass

    try:
        vm = _get_vector_memory()
        memory_ok = vm is not None
    except Exception:
        pass

    try:
        mr = _get_model_router()
        router_ok = mr is not None
    except Exception:
        pass

    all_healthy = ws_ok and memory_ok and router_ok
    return {
        "status": "healthy" if all_healthy else "degraded",
        "ws_connected": ws_ok,
        "agents": agents_status,
        "skills_loaded": skills_count,
        "memory_ok": memory_ok,
        "model_router": "ready" if router_ok else "down",
        "uptime_seconds": round(time.time() - _start_time),
    }


# ─── WebSocket ─────────────────────────────────

@app.get("/system/status")
async def system_status():
    """Host measurements; unavailable sensors are represented by null."""
    status = {
        "cpu_percent": None, "memory_percent": None, "disk_percent": None,
        "memory_used_bytes": None, "memory_total_bytes": None,
        "disk_used_bytes": None, "disk_total_bytes": None,
        "uptime_seconds": max(0, round(time.time() - _start_time)),
    }
    try:
        disk = shutil.disk_usage(Path(__file__).parent)
        status.update(disk_percent=round(disk.used / disk.total * 100, 1),
                      disk_used_bytes=disk.used, disk_total_bytes=disk.total)
    except (OSError, ZeroDivisionError):
        pass
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            status["cpu_percent"] = psutil.cpu_percent(interval=None)
        except (OSError, RuntimeError, NotImplementedError):
            pass
        try:
            memory = psutil.virtual_memory()
            status.update(memory_percent=memory.percent, memory_used_bytes=memory.used,
                          memory_total_bytes=memory.total)
        except (OSError, RuntimeError, NotImplementedError):
            pass
    return status

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    cid = str(uuid.uuid4())[:8]
    _clients[cid] = ws
    user: User | None = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object")
            except (ValueError, TypeError):
                await _send(ws, "error", {"error": "请求必须是有效的 JSON 对象"})
                continue
            token = data.get("token", "")

            if token:
                user = _get_users().get_user(token)
            if not user:
                await _send(ws, "auth", {"ok": False, "error": "请先登录"})
                continue

            msg = data.get("message", "")
            if not isinstance(msg, str):
                await _send(ws, "error", {"error": "message 必须是文本"})
                continue
            msg = msg.strip()
            if not msg:
                continue

            if user.role == Role.GUEST:
                await _send(ws, "error", {"error": "游客只能查看，无法输入指令"})
                continue

            task_id = str(uuid.uuid4())[:8]
            session_id = data.get("session_id")
            try:
                _load_session(session_id)
                lock = _session_locks.setdefault(session_id, asyncio.Lock())
                async with lock:
                    session = _load_session(session_id)
                    history = list(session.messages)
                    _append_message(session_id, "user", msg)
                    if _is_complex(msg) or _needs_office_agent(msg):
                        await _run_full(ws, task_id, msg, session_id)
                    else:
                        try:
                            await _run_fast(ws, task_id, msg, session_id, history)
                        finally:
                            _agent_status["general_agent"] = "idle"
            except Exception as exc:
                error = exc.detail if isinstance(exc, HTTPException) else str(exc)
                await _send(ws, "error", {
                    "task_id": task_id, "session_id": session_id, "error": error,
                })

    except WebSocketDisconnect:
        pass
    finally:
        _clients.pop(cid, None)


# ─── Metrics API ───────────────────────────────

@app.get("/metrics/summary")
async def metrics_summary(last_n: int = 50):
    return get_metrics_tracker().summary(last_n)

@app.get("/metrics/trend")
async def metrics_trend(metric: str = "latency", last_n: int = 20):
    return get_metrics_tracker().trend(metric, last_n)

@app.get("/metrics/agent/{name}")
async def agent_metrics(name: str, last_n: int = 20):
    return get_metrics_tracker().agent_report(name, last_n)


# ─── History ───────────────────────────────────

@app.get("/history")
async def history():
    return [{"task_id": r.run_id, "description": r.task_description,
             "status": "completed" if r.fail_count == 0 else "partial",
             "latency": r.total_latency, "tokens": r.total_tokens}
            for r in get_metrics_tracker()._runs[-50:]]


# ─── Checkpoint API ────────────────────────────

@app.get("/checkpoints")
async def list_checkpoints():
    return _get_checkpoint_store().list_all()

@app.post("/checkpoints/{cid}/resume")
async def resume_checkpoint(cid: str):
    cp = _get_checkpoint_store().load(cid)
    if not cp:
        return {"error": "Checkpoint not found"}
    return {"checkpoint_id": cid, "task_id": cp.task_id, "data": cp.data}


# ─── Model Router API ──────────────────────────

@app.get("/models")
async def list_model_configs():
    return _get_model_router().list_configs()

@app.post("/settings/models")
async def update_model_settings(req: dict):
    """更新多模型配置。Body: {enabled: bool, orchestrator: {model, base_url, api_key}, ...}"""
    s = get_settings()
    if "models" not in s._data:
        s._data["models"] = {}
    s._data["models"].update(req)
    s.save()
    _get_model_router()._refresh()
    _get_adapter(force_reload=True)  # 即时切换生效
    return {"ok": True, "models": _get_model_router().list_configs()}


# ─── Memory API ────────────────────────────────

@app.get("/memory/stats")
async def memory_stats():
    return _get_vector_memory().stats()

@app.post("/memory/search")
async def memory_search(req: dict):
    query = req.get("query", "")
    top_k = req.get("top_k", 5)
    return _get_vector_memory().search(query, top_k)

@app.post("/memory/store")
async def memory_store(req: dict):
    mid = _get_vector_memory().store(
        content=req.get("content", ""),
        category=req.get("category", "general"),
        importance=req.get("importance"),
    )
    return {"ok": True, "id": mid}


# ─── Mode ──────────────────────────────────────

@app.get("/mode")
async def mode():
    a = _get_adapter()
    return {"mode": a.mode, "backend": a.backend, "model": a.model,
            "multi_model": _get_model_router().is_multi_model_enabled()}


# ─── Agent & Skill Management ──────────────────

@app.get("/agents")
async def list_agents():
    ws, _ = _get_ws()
    agents = []
    for name in ws.agents.list_all():
        agent = ws.agents.get(name)
        if not agent:
            continue
        status = _agent_status.get(name, "idle")
        agents.append({
            "name": name, "status": status,
            "skills": [{"name": s.contract.name, "capabilities": s.contract.capabilities}
                       for s in agent._skills.values()],
            "all_skills": agent.skill_names,
            "system_prompt_preview": agent.system_prompt[:100],
        })
    return agents

@app.post("/agents/{name}/rename")
async def rename_agent(name: str, req: dict):
    ws, _ = _get_ws()
    agent = ws.agents.get(name)
    if not agent:
        return {"error": "Agent not found"}
    new_name = req.get("new_name", "").strip()
    if not new_name or new_name == name:
        return {"error": "Invalid name"}
    ws.agents._agents[new_name] = ws.agents._agents.pop(name)
    agent.name = new_name
    _agent_status[new_name] = _agent_status.pop(name, "idle")
    return {"ok": True, "new_name": new_name}

@app.post("/agents/{name}/skills")
async def update_agent_skills(name: str, req: dict):
    ws, _ = _get_ws()
    agent = ws.agents.get(name)
    if not agent:
        return {"error": "Agent not found"}
    wanted = set(req.get("skills", []))
    current = set(agent.skill_names)
    for sk_name in wanted - current:
        skill = ws.skills.get(sk_name)
        if skill:
            agent._skills[sk_name] = skill
    for sk_name in current - wanted:
        agent._skills.pop(sk_name, None)
    return {"ok": True, "skills": agent.skill_names}

@app.post("/agents/create")
async def create_agent(req: dict):
    ws, _ = _get_ws()
    name = req.get("name", "").strip()
    if not name or name in ws.agents._agents:
        return {"error": "Name required and must be unique"}
    from framework.agent.base import Agent
    skill_names = req.get("skills", [])
    skills = []
    for sn in skill_names:
        s = ws.skills.get(sn)
        if s:
            skills.append(s)
    prompt = req.get("system_prompt", f"You are {name}. Respond in Chinese (中文).")
    agent = Agent(name=name, skills=skills, system_prompt=prompt, memory_scope=10)
    ws.agents.register(agent)
    _agent_status[name] = "idle"
    return {"ok": True, "name": name, "skills": agent.skill_names}

@app.get("/agents/{name}/thread")
async def agent_thread(name: str):
    """返回指定 Agent 的执行线程——任务历史和工具调用记录。"""
    tracker = get_metrics_tracker()
    runs = []
    for r in tracker._runs[-200:]:
        for st in (r.subtasks or []):
            if st.get("agent") == name:
                runs.append({
                    "run_id": r.run_id,
                    "description": r.task_description,
                    "subtask": st.get("description", ""),
                    "status": st.get("status", "unknown"),
                    "latency": st.get("latency", 0),
                    "tool_calls": st.get("tool_calls", []),
                    "timestamp": r.start_time,
                })
    return {"agent": name, "threads": runs[-20:], "total": len(runs)}


@app.get("/skills")
async def list_skills():
    ws, _ = _get_ws()
    return [
        {"name": name, "capabilities": s.contract.capabilities,
         "cost": s.contract.cost_score, "quality": s.contract.quality_score,
         "latency": s.contract.latency_score}
        for name, s in ws.skills._skills.items()
    ]


# ─── Auth ──────────────────────────────────────

@app.post("/auth/login")
async def api_login(req: dict):
    um = _get_users()
    t, u = um.login(req.get("username", ""), req.get("password", ""))
    if t:
        return {"ok": True, "token": t, "user": u.to_safe_dict()}
    return {"ok": False, "error": "用户名或密码错误"}

@app.post("/auth/users")
async def create_user(req: dict):
    return _get_users().create_user(req.get("token", ""), req.get("username", ""),
                                    req.get("password", ""), req.get("role", "guest"),
                                    req.get("display_name", ""))

@app.get("/auth/users")
async def list_users_api(token: str = ""):
    return _get_users().list_users(token)

@app.post("/auth/users/{uid}/role")
async def set_user_role(uid: str, req: dict):
    return _get_users().set_role(req.get("token", ""), uid, req.get("role", "guest"))

@app.delete("/auth/users/{uid}")
async def delete_user_api(uid: str, token: str = ""):
    return _get_users().delete_user(token, uid)


# ─── Settings ──────────────────────────────────

@app.get("/settings")
async def get_all_settings():
    return get_settings().get_all()

@app.post("/settings/llm")
async def update_llm_settings(req: dict):
    s = get_settings()
    s.update_llm(**{k: v for k, v in req.items()
                    if k in ("model", "base_url", "api_key", "temperature", "max_tokens", "provider")})
    _get_adapter(force_reload=True)
    return {"ok": True, "llm": {"model": s.llm_model, "provider": s.llm_provider, "base_url": s.llm_base_url}}

@app.post("/settings/search")
async def update_search_settings(req: dict):
    s = get_settings()
    s.update_search(**{k: v for k, v in req.items()
                       if k in ("tavily_api_key", "brave_api_key", "bing_api_key",
                                "serpapi_api_key", "jina_enabled", "fallback_order")})
    return {"ok": True}


# ─── Sessions ──────────────────────────────────

@app.get("/sessions")
async def list_sessions():
    return _get_sessions().list_all()

@app.post("/sessions/create")
async def create_session(req: dict):
    name = req.get("name", "")
    if not isinstance(name, str) or len(name.strip()) > 120:
        raise HTTPException(status_code=400, detail="name must be text of at most 120 characters")
    s = _get_sessions().create(name.strip())
    _get_sessions().save(s)
    return {"session_id": s.session_id, "name": s.name,
            "created_at": s.created_at, "updated_at": s.updated_at}

@app.post("/sessions/{sid}/load")
async def load_session(sid: str):
    s = _load_session(sid)
    return {"session_id": s.session_id, "name": s.name, "messages": s.messages,
            "message_count": len(s.messages), "created_at": s.created_at, "updated_at": s.updated_at}


@app.post("/sessions/{sid}/rename")
async def rename_session(sid: str, req: dict):
    _load_session(sid)
    name = req.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise HTTPException(status_code=400, detail="name must contain 1 to 120 characters")
    s = _get_sessions().rename(sid, name.strip())
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "session_id": s.session_id, "name": s.name, "updated_at": s.updated_at}

@app.post("/sessions/message")
async def save_message(req: dict):
    session_id = req.get("session_id")
    _load_session(session_id)
    role, content, tool_calls = req.get("role", "assistant"), req.get("content", ""), req.get("tool_calls")
    if role not in ("user", "assistant") or not isinstance(content, str):
        raise HTTPException(status_code=400, detail="A user/assistant role and text content are required")
    if tool_calls is not None and not isinstance(tool_calls, list):
        raise HTTPException(status_code=400, detail="tool_calls must be a list")
    _append_message(session_id, role, content, tool_calls)
    return {"ok": True, "session_id": session_id}

@app.delete("/sessions/{sid}")
async def delete_session(sid: str):
    """删除会话。"""
    _load_session(sid)
    _get_sessions().delete(sid)
    return {"ok": True}


# ─── Persistence (SQLite) ──────────────────────

@app.get("/persistence/summary")
async def persistence_summary():
    store = get_store()
    return store.get_cost_summary()

@app.get("/persistence/metrics")
async def persistence_metrics(agent: str = "", limit: int = 50):
    store = get_store()
    return store.get_metrics(agent=agent or None, limit=limit)


# ─── Local Search ──────────────────────────────

@app.get("/search/local")
async def local_search(q: str = ""):
    """快速本地搜索——不调 LLM，毫秒级返回。"""
    if len(q) < 2:
        return {"results": [], "query": q}
    results = []; ql = q.lower()
    try:
        for s in _get_sessions().list_all():
            if ql in (s.get("name","") or "").lower() or ql in s.get("session_id",""):
                results.append({"type":"session","id":s.get("session_id",""),"title":s.get("name","未命名"),"detail":str(s.get("message_count",0))+" 条消息"})
    except: pass
    try:
        ws,_=_get_ws()
        for name in ws.agents.list_all():
            if ql in name.lower():
                a=ws.agents.get(name)
                results.append({"type":"agent","id":name,"title":name,"detail":"技能: "+str(len(a.skill_names) if a else 0)+" · "+_agent_status.get(name,"idle")})
    except: pass
    try:
        ws,_=_get_ws()
        for name,s in ws.skills._skills.items():
            if ql in name.lower() or any(ql in c.lower() for c in s.contract.capabilities):
                results.append({"type":"skill","id":name,"title":name,"detail":", ".join(s.contract.capabilities[:3])})
    except: pass
    try:
        for r in get_metrics_tracker()._runs[-100:]:
            d=r.task_description or ""
            if ql in d.lower(): results.append({"type":"task","id":r.run_id,"title":d[:60],"detail":f"延迟:{r.total_latency:.1f}s Token:{r.total_tokens}"})
    except: pass
    return {"results":results[:20],"query":q,"total":len(results)}


# ─── Slash Commands ────────────────────────────

@app.post("/slash")
async def slash_command(req: dict):
    cmd = req.get("command", "").lstrip("/")
    context = req.get("context", "")
    adapter = _get_adapter()

    prompts = {
        "review": "Review the following code changes. Identify issues, suggest improvements, and rate quality (1-10). Reply in Chinese.",
        "summarize": "Summarize the following conversation into 3-5 key points. Reply in Chinese.",
        "commit": "Generate a concise git commit message (under 70 chars) for these changes. Only output the message, nothing else.",
        "test": "Suggest test cases for the following code. Include edge cases. Reply in Chinese.",
        "search": "Search across sessions, tasks, and files for the following query. Return relevant results with summaries. Reply in Chinese.",
    }

    prompt = prompts.get(cmd, "Process this: " + cmd)
    result = await adapter.run(
        system_prompt=prompt,
        messages=[{"role": "user", "content": context[:3000]}],
        temperature=0.3, max_tokens=500 if cmd != "commit" else 100,
    )

    if cmd == "commit" and context:
        import subprocess
        try:
            msg = result["content"].strip().replace('"', '').replace("'", "")[:70]
            r = subprocess.run(["git", "add", "-A"], capture_output=True, cwd=".")
            r = subprocess.run(["git", "commit", "-m", msg], capture_output=True, cwd=".")
            if r.returncode == 0:
                result["content"] += f"\n\nCommitted: {msg}"
            else:
                result["content"] += f"\n\nCommit failed: {r.stderr.decode()[:200]}"
        except Exception as e:
            result["content"] += f"\n\nGit error: {e}"

    return {"command": cmd, "result": result["content"]}


# ─── Startup ───────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    a = _get_adapter()
    mr = _get_model_router()
    print(f"  Backend: {a.backend} | Mode: {a.mode} | Multi-model: {mr.is_multi_model_enabled()}")
    print(f"  http://localhost:{_port}")
    yield

app.router.lifespan_context = lifespan
_port = 8000

if __name__ == "__main__":
    import uvicorn
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    if len(sys.argv) > 2:
        os.environ["DEEPSEEK_API_KEY"] = sys.argv[2]
    uvicorn.run(app, host="127.0.0.1", port=_port, log_level="warning")
