from __future__ import annotations

import os
import asyncio
import sys
from pathlib import Path

import pytest

from framework.agent.base import AgentContext
from framework.agent.general import GeneralAgent
from framework.agent.code import CodeAgent
from framework.agent.office import OfficeAgent
from framework.agent.research import ResearchAgent
from framework.orchestrator.orchestrator import OrchestratorAgent
from framework.skill.claude_tools import BashSkill, EditSkill, GlobSkill, GrepSkill, ReadSkill, SpawnAgentSkill, TodoWriteSkill, WriteSkill
from framework.skill.code import CodeSkill
from framework.skill.office_tools import CalendarSkill, ClipboardSkill, DocumentSkill, EmailSkill, TranslateSkill
from framework.skill.search import SearchSkill
from framework.skill.web_search import WebSearchSkill
from framework.sdk.openai_adapter import OpenAIAdapter


@pytest.mark.asyncio
async def test_bash_runs_in_workspace_with_a_shell_that_exists(tmp_path, monkeypatch):
    root = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(root))
    monkeypatch.chdir(elsewhere)

    command = "pwd -W" if os.name == "nt" else "pwd"
    result = await BashSkill().execute(command=command, timeout=10_000)

    assert result["exit_code"] == 0
    assert Path(result["stdout"].strip()).resolve() == root.resolve()


@pytest.mark.asyncio
async def test_file_tools_resolve_relative_paths_from_workspace(tmp_path, monkeypatch):
    root = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(root))
    monkeypatch.chdir(elsewhere)

    written = await WriteSkill().execute(file_path="src/note.txt", content="first\nsecond\n")
    read = await ReadSkill().execute(file_path="src/note.txt", offset=2, limit=1)
    edited = await EditSkill().execute(file_path="src/note.txt", old_string="second", new_string="updated")
    globbed = await GlobSkill().execute(pattern="**/*.txt")
    grepped = await GrepSkill().execute(pattern="updated", path="src", glob="*.txt")

    assert written["written"] is True
    assert (root / "src" / "note.txt").read_text(encoding="utf-8") == "first\nupdated\n"
    assert read["content"].endswith("2\tsecond")
    assert edited["modified"] is True
    assert globbed["count"] == 1 and "src/note.txt" in globbed["matches"][0].replace("\\", "/")
    assert grepped["count"] == 1 and "updated" in grepped["matches"][0]


@pytest.mark.asyncio
async def test_write_file_reports_missing_path_without_touching_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    result = await WriteSkill().execute(file_path="", content="must not be written")

    assert result["written"] is False
    assert "file_path" in result
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_edit_file_rejects_empty_old_text(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    await WriteSkill().execute(file_path="note.txt",content="")

    result=await EditSkill().execute(file_path="note.txt",old_string="",new_string="broken")

    assert result["modified"] is False
    assert (tmp_path/"note.txt").read_text(encoding="utf-8")==""


def test_code_agent_registers_every_file_tool_it_advertises():
    agent = CodeAgent()

    schemas = {schema["name"] for schema in agent._code_skill.to_tool_schemas()}
    handlers = set(agent._code_skill.get_tool_handlers())

    assert {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "apply_changes"} <= schemas
    assert schemas == handlers


@pytest.mark.asyncio
async def test_code_change_summary_cannot_claim_files_without_successful_writes():
    result = await CodeSkill().handle_apply_changes(
        task_description="refactor", changes="updated the implementation", files_modified=["main.py"], review_notes="done"
    )

    assert result["files_modified"] == []
    assert result["error"]


@pytest.mark.asyncio
async def test_code_change_summary_uses_successful_file_tool_results(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    skill = CodeSkill()
    await skill.get_tool_handlers()["write_file"](file_path="main.py", content="print('ok')")

    result = await skill.handle_apply_changes(
        task_description="refactor", changes="updated the implementation", files_modified=["main.py", "fake.py"], review_notes="done"
    )

    assert result["files_modified"] == ["main.py"]


@pytest.mark.asyncio
async def test_code_skill_does_not_report_mock_changes_as_real():
    result = await CodeSkill().execute(task_description="refactor", target_file="main.py")

    assert result.get("error")
    assert result["files_modified"] == []


@pytest.mark.asyncio
async def test_code_agent_does_not_carry_modified_files_into_the_next_task(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    class Adapter:
        calls = 0

        async def run(self, **kwargs):
            self.calls += 1
            tool_calls = []
            if self.calls == 1:
                output = await kwargs["tool_handlers"]["write_file"](
                    file_path="first.py", content="print('first')"
                )
                tool_calls.append({"name": "write_file", "output": output})
            else:
                output = await kwargs["tool_handlers"]["apply_changes"](
                    task_description="review only", changes="no changes", files_modified=["first.py"], review_notes=""
                )
                tool_calls.append({"name": "apply_changes", "output": output})
            return {"content": "done", "tool_calls": tool_calls, "usage": {}, "mode": "test"}

    agent = CodeAgent(Adapter())
    first = await agent.execute(AgentContext("code_agent", "one", {"task_description": "create first"}))
    second = await agent.execute(AgentContext("code_agent", "two", {"task_description": "review only"}))

    assert first["files_modified"] == ["first.py"]
    assert second["files_modified"] == []


@pytest.mark.asyncio
async def test_code_agent_reports_incomplete_when_requested_fix_changes_no_file():
    class Adapter:
        async def run(self, **kwargs):
            return {"content": "已经修复", "tool_calls": [], "usage": {}, "mode": "test"}
    agent = CodeAgent(Adapter())

    result = await agent.execute(AgentContext(
        "code_agent", "fix", {"task_description": "修复代码中的错误"}
    ))

    assert result["files_modified"] == []
    assert result.get("error")


def test_office_requests_are_selected_for_the_specialized_fast_path():
    from server import _needs_office_agent

    assert _needs_office_agent("把这段话翻译成英文")
    assert _needs_office_agent("给我发一封邮件")
    assert not _needs_office_agent("给我做一个网页版时钟")


@pytest.mark.asyncio
async def test_research_agent_uses_real_search_skill_instead_of_sample_urls(monkeypatch):
    agent = ResearchAgent()

    async def search(query, max_results=5):
        return {"query": query, "provider": "test", "results": [{"title": "real result", "url": "https://news.example/article", "content": "fresh data", "score": 1}]}

    monkeypatch.setattr(agent._search_skill, "_search", search)
    result = await agent._search_skill.execute(query="latest update")

    assert result["results"][0]["url"] == "https://news.example/article"
    assert "example.com/research" not in str(result)


@pytest.mark.asyncio
async def test_research_agent_returns_sources_when_model_summary_stalls(monkeypatch):
    class SlowAdapter:
        mode = "live"
        async def run(self, **kwargs):
            await asyncio.sleep(1)
            return {"content": "unreachable", "tool_calls": [], "usage": {}, "mode": "live"}
    agent = ResearchAgent(SlowAdapter())
    agent.SUMMARY_TIMEOUT = 0.02
    async def search(**kwargs):
        return {"results": [{"title": "Official docs", "url": "https://python.org/docs", "content": "Python documentation"}],
                "query": kwargs["query"], "provider": "test"}
    monkeypatch.setattr(agent._search_skill, "execute", search)

    result = await asyncio.wait_for(agent.execute(AgentContext(
        "research_agent", "1", {"query": "Python docs"}
    )), timeout=0.5)

    assert result["error"] is None
    assert result["sources"] == ["https://python.org/docs"]
    assert "Official docs" in result["summary"]


@pytest.mark.asyncio
async def test_search_without_provider_returns_actionable_error_not_fake_result(monkeypatch):
    class Settings:
        search_fallback_order = ["brave"]

    async def unavailable(*args, **kwargs):
        return None

    monkeypatch.setattr("framework.settings.get_settings", lambda: Settings())
    monkeypatch.setattr(WebSearchSkill, "_try_provider", unavailable)
    result = await SearchSkill().execute(query="latest update")

    assert result["error"]
    assert result["sources"] == []
    assert "example.com" not in str(result)


@pytest.mark.asyncio
async def test_search_fetch_propagates_provider_error(monkeypatch):
    async def unavailable(*args, **kwargs):
        return {"results": [], "query": "latest update", "provider": "none", "error": "network unavailable"}
    monkeypatch.setattr(WebSearchSkill,"_search",unavailable)

    result=await WebSearchSkill().execute_fetch(query="latest update")

    assert result["error"] == "network unavailable"
    assert result["fetched"] == []
    assert result["summary"] == ""


@pytest.mark.asyncio
async def test_search_fetch_marks_empty_page_as_not_fetched(monkeypatch):
    async def search(self, query, count):
        return {"query": query, "provider": "test", "results": [
            {"title": "Result", "url": "https://example.org/article", "content": "Search snippet"}
        ]}
    async def empty(self, url):
        return ""
    monkeypatch.setattr(WebSearchSkill, "_search", search)
    monkeypatch.setattr(WebSearchSkill, "_fetch_url", empty)

    result = await WebSearchSkill().execute_fetch(query="test", fetch_count=1)

    assert result["fetched"][0]["fetched"] is False
    assert result["fetched"][0]["content"] == "Search snippet"


@pytest.mark.asyncio
async def test_search_fetch_opens_independent_pages_concurrently(monkeypatch):
    async def search(self, query, count):
        return {"query": query, "provider": "test", "results": [
            {"title": str(i), "url": f"https://example.org/{i}", "content": "snippet"}
            for i in (1, 2)
        ]}
    both_started = asyncio.Event()
    started = []
    async def fetch(self, url):
        started.append(url)
        if len(started) == 2:
            both_started.set()
        await both_started.wait()
        return url
    monkeypatch.setattr(WebSearchSkill, "_search", search)
    monkeypatch.setattr(WebSearchSkill, "_fetch_url", fetch)

    result = await asyncio.wait_for(WebSearchSkill().execute_fetch(query="test", fetch_count=2), timeout=0.5)

    assert [page["fetched"] for page in result["fetched"]] == [True, True]


@pytest.mark.asyncio
async def test_search_tries_keyless_bing_after_configured_providers_fail(monkeypatch):
    import framework.skill.web_search as search_module
    class Settings:
        search_fallback_order = ["baidu", "brave", "tavily"]
    attempted = []
    async def provider(self, name, query, count, settings):
        attempted.append(name)
        if name == "tavily":
            return {"query": query, "provider": "tavily", "results": []}
        if name == "bing":
            return {"query": query, "provider": "bing", "results": [
                {"title": "Python release", "url": "https://python.org/", "content": "Release notes", "score": 1.0}
            ]}
        return None
    monkeypatch.setattr("framework.settings.get_settings", lambda: Settings())
    monkeypatch.setattr(WebSearchSkill, "_try_provider", provider)
    search_module._search_cache.clear()

    result = await WebSearchSkill().execute(query="python release unique fallback")

    assert result["provider"] == "bing"
    assert attempted[-1] == "bing"


@pytest.mark.asyncio
async def test_chinese_search_can_use_configured_brave_provider(monkeypatch):
    import framework.skill.web_search as search_module
    class Settings:
        search_fallback_order = ["brave"]
    tried = []
    async def provider(self, name, query, count, settings):
        tried.append(name)
        if name == "brave":
            return {"query": query, "provider": "brave", "results": [
                {"title": "资料", "url": "https://example.org/info", "content": "内容", "score": 1}
            ]}
        return None
    monkeypatch.setattr("framework.settings.get_settings", lambda: Settings())
    monkeypatch.setattr(WebSearchSkill, "_try_provider", provider)
    search_module._search_cache.clear()

    result = await WebSearchSkill().execute(query="中文搜索资料")

    assert result["provider"] == "brave"
    assert tried == ["brave"]


@pytest.mark.asyncio
async def test_slow_search_provider_is_bounded_and_falls_through(monkeypatch):
    import framework.skill.web_search as search_module
    class Settings:
        search_fallback_order = ["baidu"]
    async def slow_provider(self, name, query, count, settings):
        if name == "baidu":
            await asyncio.sleep(1)
        return None
    monkeypatch.setattr("framework.settings.get_settings", lambda: Settings())
    monkeypatch.setattr(WebSearchSkill, "_try_provider", slow_provider)
    monkeypatch.setattr(WebSearchSkill, "PROVIDER_TIMEOUT", 0.02, raising=False)
    search_module._search_cache.clear()

    result = await asyncio.wait_for(WebSearchSkill().execute(query="unique slow source test"), timeout=0.2)

    assert result["error"]


@pytest.mark.asyncio
async def test_echo_only_calls_the_tool_requested_by_the_user():
    called = []
    async def handler(**kwargs):
        called.append(kwargs)
        return {"results": [], "query": kwargs["query"], "error": "test provider unavailable"}
    async def file_handler(**kwargs):
        raise AssertionError("unrelated file tool was called")
    tools = [WebSearchSkill().to_tool_schema(), WriteSkill().to_tool_schema()]

    result = await OpenAIAdapter(api_key="")._run_echo(
        "", [{"role": "user", "content": "搜索 python 发布说明"}], tools,
        {"web_search": handler, "write_file": file_handler},
    )

    assert len(called) == 1
    assert [call["name"] for call in result["tool_calls"]] == ["web_search"]


@pytest.mark.asyncio
async def test_live_model_requests_have_a_bounded_timeout(monkeypatch):
    from types import SimpleNamespace
    import framework.sdk.openai_adapter as adapter_module
    options = {}
    class Client:
        def __init__(self, **kwargs):
            options.update(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        async def create(self, **kwargs):
            message = SimpleNamespace(content="done", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None, model="test")
    monkeypatch.setattr(adapter_module, "AsyncOpenAI", Client)

    result = await OpenAIAdapter(api_key="test")._run_live("", [{"role": "user", "content": "hi"}], None, None, 0.3, 10, "test")

    assert result["content"] == "done"
    assert 0 < options["timeout"] <= 30
    assert options["max_retries"] == 0


def test_office_requests_route_to_office_worker_and_tool():
    plan = OrchestratorAgent()._plan_echo("把这段话翻译成英文", {
        "general_agent": {}, "research_agent": {}, "code_agent": {}, "office_agent": {},
    })

    assert plan["tasks"][0]["agent"] == "office_agent"
    assert plan["tasks"][0]["skill"] == "translate"


def test_agent_tool_schemas_match_registered_handlers():
    for agent in (GeneralAgent(), CodeAgent(), OfficeAgent(), ResearchAgent()):
        schemas = {schema["name"] for schema in agent.get_all_tool_schemas() if "name" in schema}
        handlers = set(agent.get_all_tool_handlers())
        assert schemas == handlers, agent.name


@pytest.mark.asyncio
async def test_worker_does_not_mark_failed_tool_call_as_success():
    class Adapter:
        async def run(self, **kwargs):
            return {"content": "已完成", "tool_calls": [{
                "name": "email", "input": {}, "output": {"error": "SMTP unavailable"},
            }], "usage": {}, "mode": "test"}
    office = await OfficeAgent(Adapter()).execute(AgentContext(
        "office_agent", "mail", {"query": "发送邮件"}
    ))
    general = await GeneralAgent(Adapter()).execute(AgentContext(
        "general_agent", "mail", {"query": "发送邮件"}
    ))

    assert "SMTP unavailable" in office.get("error", "")
    assert "SMTP unavailable" in general.get("error", "")


def test_echo_summary_does_not_turn_tool_errors_into_success():
    summary=OpenAIAdapter()._echo_synthesize("", "translate this", [
        {"name":"translate","output":{"translated":"","error":"online model is not configured"}},
    ])

    assert "执行失败" in summary
    assert "online model is not configured" in summary


@pytest.mark.asyncio
async def test_spawn_agent_keeps_task_as_query_when_additional_input_is_given():
    class Child:
        name = "research_agent"
        async def execute(self, context):
            return {"query": context.input_data.get("query"), "extra": context.input_data.get("extra")}

    class Registry:
        def get(self, name): return Child() if name == "research_agent" else None

    skill = SpawnAgentSkill()
    skill._registry = Registry()
    result = await skill.execute(agents=[{"agent_type":"research_agent","task":"find sources","input":{"extra":"kept"}}])

    assert result["results"][0] == {"agent":"research_agent","query":"find sources","extra":"kept"}


@pytest.mark.asyncio
async def test_documents_save_under_workspace_and_escape_html(tmp_path, monkeypatch):
    root = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(root))
    monkeypatch.chdir(elsewhere)

    result = await DocumentSkill().execute(title="<Report>", content="<script>bad()</script>", format="html")

    output = (root / result["file_path"]).read_text(encoding="utf-8")
    assert result["file_path"].startswith("documents/")
    assert "&lt;script&gt;" in output
    assert "<script>bad()" not in output


@pytest.mark.asyncio
async def test_calendar_events_survive_skill_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))

    added = await CalendarSkill().execute(action="add", title="Review", date="2026-10-06", time="14:30")
    listed = await CalendarSkill().execute(action="list")

    assert added["status"] == "added"
    assert listed["events"][0]["title"] == "Review"


@pytest.mark.asyncio
async def test_calendar_export_writes_ical_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    skill = CalendarSkill()
    await skill.execute(action="add", title="Review", date="2026-10-06", time="14:30")

    result = await skill.execute(action="export")

    assert result["status"] == "exported"
    assert (tmp_path / result["file_path"]).read_text(encoding="utf-8").startswith("BEGIN:VCALENDAR")


@pytest.mark.asyncio
async def test_translation_does_not_claim_echoed_text_is_translated():
    result = await TranslateSkill().execute(text="hello", target="zh")

    assert result.get("error")
    assert result.get("translated") != "hello"


@pytest.mark.asyncio
async def test_translation_uses_live_adapter_when_configured():
    class Adapter:
        mode = "live"
        async def run(self, **kwargs): return {"content":"你好"}

    result = await TranslateSkill(Adapter()).execute(text="hello", target="zh")

    assert result["translated"] == "你好"


@pytest.mark.asyncio
async def test_todo_list_is_saved_and_loaded_again(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    item = {"content":"check tools","status":"in_progress","activeForm":"checking tools"}
    result = await TodoWriteSkill().execute(todos=[item])
    reloaded = TodoWriteSkill()

    assert result["todos"] == [item]
    assert reloaded._todos == [item]


@pytest.mark.asyncio
async def test_clipboard_uses_the_platform_command_and_reports_failure(monkeypatch):
    from types import SimpleNamespace
    import framework.skill.office_tools as office_tools

    monkeypatch.setattr(office_tools.sys, "platform", "win32")
    calls=[]
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="clipboard text", stderr="")
    monkeypatch.setattr(office_tools.subprocess, "run", run)
    tool=ClipboardSkill()
    written=await tool.execute(action="write",content="clipboard text")
    read=await tool.execute(action="read")

    assert written["status"] == "ok" and calls[0] == ["clip.exe"]
    assert read["content"] == "clipboard text"


@pytest.mark.asyncio
async def test_email_draft_is_saved_to_workspace(tmp_path, monkeypatch):
    root = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(root))
    monkeypatch.chdir(elsewhere)

    result = await EmailSkill().execute(action="draft", to="person@example.com", subject="Hello", body="Draft body")

    assert result["status"] == "draft"
    assert (root / result["file_path"]).is_file()
