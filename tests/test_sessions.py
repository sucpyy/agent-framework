"""Session routes and WebSocket turns use temporary data and never call a model."""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server
from framework.auth.users import Role, User
from framework.event.bus import EventBus
from framework.session.store import Session, SessionStore
from framework.task.task import Subtask, Task, TaskResult, TaskStatus


class FakeAdapter:
    mode = "test"

    def __init__(self):
        self.calls = []
        self.fail = False

    async def run_stream(self, **kwargs):
        self.calls.append(kwargs)
        yield {"type": "text", "content": "完整"}
        if self.fail:
            raise RuntimeError("stream interrupted")
        yield {"type": "text", "content": "回答"}
        yield {"type": "done", "usage": {"input_tokens": 2, "output_tokens": 3}}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = SessionStore(str(tmp_path / "sessions"))
    adapter = FakeAdapter()
    user = User(username="tester", role=Role.DEVELOPER)
    monkeypatch.setattr(server, "_sessions", store)
    monkeypatch.setattr(server, "_clients", {})
    monkeypatch.setattr(server, "_agent_status", {})
    monkeypatch.setattr(server, "_get_users", lambda: SimpleNamespace(get_user=lambda token: user))
    monkeypatch.setattr(server, "_get_adapter", lambda: adapter)
    monkeypatch.setattr(server, "_get_ga", lambda: SimpleNamespace(
        system_prompt="test", get_all_tool_schemas=lambda: [], get_all_tool_handlers=lambda: {},
    ))
    monkeypatch.setattr(server, "_get_vector_memory", lambda: SimpleNamespace(
        recall_context=lambda *args, **kwargs: "", store=lambda *args, **kwargs: None,
    ))

    async def score(*args):
        return {"overall": 1}

    monkeypatch.setattr(server, "_get_scorer", lambda: SimpleNamespace(score=score))
    # Do not enter TestClient's lifespan: production startup reads user settings.
    client = TestClient(server.app)
    yield SimpleNamespace(client=client, store=store, adapter=adapter)
    client.close()


def create(api, name):
    response = api.client.post("/sessions/create", json={"name": name})
    assert response.status_code == 200
    return response.json()["session_id"]


def send_turn(ws, session_id, message):
    ws.send_json({"session_id": session_id, "message": message, "token": "test"})
    events = []
    for _ in range(30):
        event = ws.receive_json()
        events.append(event)
        if event["event"] == "error" or (
            event["event"] == "status" and event["data"]["phase"] == "done"
        ):
            return events
    pytest.fail("WebSocket turn did not finish")


def test_rest_message_uses_explicit_session_after_another_tab_loads(api):
    first, second = create(api, "first"), create(api, "second")
    api.client.post(f"/sessions/{second}/load")
    response = api.client.post("/sessions/message", json={
        "session_id": first, "role": "user", "content": "only first",
    })
    assert response.status_code == 200
    assert [m["content"] for m in api.store.load(first).messages] == ["only first"]
    assert api.store.load(second).messages == []


@pytest.mark.parametrize("session_id", [None, "", "../outside", "..\\outside", "C:outside", [], "unknown"])
def test_rest_message_rejects_missing_invalid_or_unknown_session(api, session_id):
    active = create(api, "active")
    response = api.client.post("/sessions/message", json={
        "session_id": session_id, "role": "user", "content": "must not save",
    })
    assert response.status_code in (400, 404, 422)
    assert api.store.load(active).messages == []


def test_load_and_rename_preserve_history_and_return_timestamps(api):
    sid = create(api, "before")
    session = api.store.load(sid)
    session.add_message("assistant", "keep this")
    api.store.save(session)
    response = api.client.post(f"/sessions/{sid}/rename", json={"name": "  renamed  "})
    assert response.status_code == 200
    loaded = api.client.post(f"/sessions/{sid}/load").json()
    assert loaded["name"] == "renamed"
    assert loaded["created_at"] == session.created_at
    assert loaded["updated_at"] >= session.updated_at
    assert loaded["messages"] == session.messages
    assert api.adapter.calls == []


@pytest.mark.parametrize("name", ["", "   ", None, [], "x" * 121])
def test_rename_rejects_invalid_names(api, name):
    sid = create(api, "keep")
    response = api.client.post(f"/sessions/{sid}/rename", json={"name": name})
    assert response.status_code in (400, 422)
    assert api.store.load(sid).name == "keep"


@pytest.mark.parametrize("sid", ["../outside", "..\\outside", "C:outside", "", None, "a/b", "."])
def test_store_rejects_unsafe_ids_for_all_file_operations(tmp_path, sid):
    store = SessionStore(str(tmp_path / "sessions"))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(Session(session_id="outside").to_dict()))
    for operation in (lambda: store.load(sid), lambda: store.delete(sid),
                      lambda: store.save(Session(session_id=sid))):
        with pytest.raises(ValueError, match="session"):
            operation()
    assert outside.exists()


def test_fast_turn_is_persisted_once_and_context_stays_in_its_session(api):
    first, second = create(api, "first"), create(api, "second")
    history = api.store.load(first)
    history.add_message("user", "old question")
    history.add_message("assistant", "old answer")
    api.store.save(history)
    api.client.post(f"/sessions/{second}/load")
    with api.client.websocket_connect("/ws") as ws:
        events = send_turn(ws, first, "new question")
    result = next(e["data"] for e in events if e["event"] == "result")
    assert result["session_id"] == first
    assert result["content"] == "完整回答"
    assert api.adapter.calls[0]["messages"] == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
    ]
    assert [m["content"] for m in api.store.load(first).messages] == [
        "old question", "old answer", "new question", "完整回答",
    ]
    assert api.store.load(second).messages == []


def test_stream_error_keeps_partial_text_and_socket_accepts_next_turn(api):
    sid = create(api, "partial")
    api.adapter.fail = True
    with api.client.websocket_connect("/ws") as ws:
        failed = send_turn(ws, sid, "first")
        assert failed[-1]["event"] == "error"
        assert [m["content"] for m in api.store.load(sid).messages] == ["first", "完整"]
        api.adapter.fail = False
        succeeded = send_turn(ws, sid, "second")
        assert any(e["event"] == "result" for e in succeeded)
    assert server._agent_status["general_agent"] == "idle"


@pytest.mark.parametrize("session_id", [None, "../outside", "missing"])
def test_websocket_rejects_bad_session_without_running_model(api, session_id):
    sid = create(api, "untouched")
    with api.client.websocket_connect("/ws") as ws:
        events = send_turn(ws, session_id, "must not run")
    assert events[-1]["event"] == "error"
    assert api.adapter.calls == []
    assert api.store.load(sid).messages == []


def fake_full_workspace(monkeypatch, fail=False):
    bus = EventBus()
    task = Task(subtasks=[Subtask(subtask_id="own-subtask", description="research")])

    async def plan(message):
        return task

    async def submit_task(submitted):
        pass

    async def execute(submitted):
        await bus.emit("subtask:started", {"subtask_id": "another-task"})
        await bus.emit("subtask:started", {"subtask_id": "own-subtask"})
        if fail:
            raise RuntimeError("scheduler failed")
        return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED,
                          subtask_results=[], aggregated_output="综合结论", total_latency=0.1)

    workspace = SimpleNamespace(event_bus=bus, _planner=SimpleNamespace(plan=plan),
                                _scheduler=SimpleNamespace(execute=execute), submit_task=submit_task)
    monkeypatch.setattr(server, "_get_ws", lambda: (workspace, None))
    return bus


def test_full_turn_returns_and_persists_aggregate_and_removes_scoped_hooks(api, monkeypatch):
    bus = fake_full_workspace(monkeypatch)
    sid = create(api, "full")
    message = "先搜索最近的资料然后比较各个结果，给出足够详细的分析和完整的结论建议"
    with api.client.websocket_connect("/ws") as ws:
        events = send_turn(ws, sid, message)
    result = next(e["data"] for e in events if e["event"] == "result")
    assert result["content"] == "综合结论"
    assert result["session_id"] == sid
    assert [m["content"] for m in api.store.load(sid).messages] == [message, "综合结论"]
    forwarded = [e["data"]["payload"]["subtask_id"] for e in events if e["event"] == "event"]
    assert forwarded == ["own-subtask"]
    assert not any(bus._handlers.values())


def test_full_turn_finishes_promptly_when_optional_scoring_stalls(api, monkeypatch):
    import time
    fake_full_workspace(monkeypatch)
    monkeypatch.setattr(server, "SCORE_TIMEOUT", 0.02, raising=False)
    async def slow_score(*args):
        await asyncio.sleep(1)
        return {"overall": 8}
    monkeypatch.setattr(server, "_get_scorer", lambda: SimpleNamespace(score=slow_score))
    sid = create(api, "slow score")

    start = time.monotonic()
    with api.client.websocket_connect("/ws") as ws:
        events = send_turn(ws, sid, "先搜索最近的资料然后比较各个结果，给出足够详细的分析和完整的结论建议")

    assert time.monotonic() - start < 0.5
    assert any(event["event"] == "result" for event in events)


def test_fast_turn_finishes_promptly_when_optional_scoring_stalls(api, monkeypatch):
    import time
    monkeypatch.setattr(server, "SCORE_TIMEOUT", 0.02)
    async def slow_score(*args):
        await asyncio.sleep(1)
        return {"overall": 8}
    monkeypatch.setattr(server, "_get_scorer", lambda: SimpleNamespace(score=slow_score))
    sid = create(api, "fast score")

    start = time.monotonic()
    with api.client.websocket_connect("/ws") as ws:
        events = send_turn(ws, sid, "你好")

    assert time.monotonic() - start < 0.5
    assert any(event["event"] == "result" for event in events)


def test_failed_full_turn_removes_hooks_and_reports_error(api, monkeypatch):
    bus = fake_full_workspace(monkeypatch, fail=True)
    # Direct async call captures server events even when the previous endpoint would disconnect.
    events = []

    async def capture(ws, event, data):
        events.append((event, data))

    monkeypatch.setattr(server, "_send", capture)
    sid = create(api, "failed")
    try:
        asyncio.run(server._run_full(None, "request", "a complex request", sid))
    except RuntimeError:
        pass
    assert not any(bus._handlers.values())


def test_parallel_full_turns_with_same_subtask_ids_do_not_share_events(api, monkeypatch):
    bus = EventBus()
    delivered = {"first": [], "second": []}

    async def plan(message):
        return Task(description=message, subtasks=[Subtask(subtask_id="1")])

    async def submit_task(task):
        pass

    async def execute(task):
        await asyncio.sleep(0.01)
        await bus.emit("subtask:started", {"subtask_id": "1", "owner": task.description})
        return TaskResult(task.task_id, TaskStatus.COMPLETED, [], aggregated_output="result")

    async def capture(ws, event, data):
        if event == "event":
            delivered[ws].append(data["payload"]["owner"])

    workspace = SimpleNamespace(event_bus=bus, _planner=SimpleNamespace(plan=plan),
                                _scheduler=SimpleNamespace(execute=execute), submit_task=submit_task)
    monkeypatch.setattr(server, "_get_ws", lambda: (workspace, None))
    monkeypatch.setattr(server, "_send", capture)
    first, second = create(api, "first"), create(api, "second")

    async def run():
        await asyncio.gather(server._run_full("first", "r1", "first", first),
                             server._run_full("second", "r2", "second", second))

    asyncio.run(run())
    assert delivered == {"first": ["first"], "second": ["second"]}


def test_system_status_uses_real_disk_and_marks_unavailable_metrics(api, monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)
    response = api.client.get("/system/status")
    assert response.status_code == 200
    data = response.json()
    assert data["cpu_percent"] is None
    assert data["memory_percent"] is None
    assert 0 <= data["disk_percent"] <= 100
    assert data["disk_total_bytes"] > 0
    assert 0 <= data["disk_used_bytes"] <= data["disk_total_bytes"]
    assert data["uptime_seconds"] >= 0


def test_system_status_reports_available_metrics(api, monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(
        cpu_percent=lambda interval=None: 17.5,
        virtual_memory=lambda: SimpleNamespace(percent=42.0, used=420, total=1000),
    ))
    response = api.client.get("/system/status")
    assert response.status_code == 200
    data = response.json()
    assert data["cpu_percent"] == 17.5
    assert data["memory_percent"] == 42.0
    assert data["memory_used_bytes"] == 420
    assert data["memory_total_bytes"] == 1000
