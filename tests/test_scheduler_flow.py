"""Task-level checks for the planner, scheduler, and user-visible result."""

from types import SimpleNamespace

import pytest

from framework.scheduler.scheduler import Scheduler
from framework.task.planner import Planner
from framework.task.task import Subtask, Task, TaskStatus


def scheduler_for(output):
    class Agent:
        name = "general_agent"
        _skills = {
            "read_file": SimpleNamespace(contract=SimpleNamespace(produces=["content", "file_path"])),
            "web_search": SimpleNamespace(contract=SimpleNamespace(produces=["results", "query"])),
        }

        async def execute(self, context):
            return dict(output)

    agent = Agent()
    registry = SimpleNamespace(get=lambda name: agent if name == agent.name else None)
    return Scheduler(registry, enable_checkpoint=False, enable_metrics=False,
                     enable_memory_injection=False, max_retries=0)


@pytest.mark.asyncio
async def test_multi_tool_agent_answer_reaches_user_without_unrelated_skill_validation():
    task = Task(description="回答问题", subtasks=[Subtask(
        subtask_id="1", description="回答问题", agent_type="general_agent",
        skill_name="read_file", input_data={"query": "回答问题"},
    )])

    result = await scheduler_for({"response": "文件里写着可用"}).execute(task)

    assert result.status == TaskStatus.COMPLETED
    assert task.subtasks[0].status == TaskStatus.COMPLETED
    assert "文件里写着可用" in result.aggregated_output


@pytest.mark.asyncio
async def test_tool_error_is_failed_task_and_visible_to_user():
    task = Task(description="运行失败任务", subtasks=[Subtask(
        subtask_id="1", description="运行失败任务", agent_type="general_agent",
        skill_name="bash", input_data={"query": "运行失败任务"},
    )])

    result = await scheduler_for({"error": "command exited 1"}).execute(task)

    assert result.status == TaskStatus.FAILED
    assert task.subtasks[0].status == TaskStatus.FAILED
    assert "command exited 1" in result.aggregated_output


@pytest.mark.asyncio
async def test_optional_empty_error_field_does_not_fail_research_result():
    task = Task(description="查资料", subtasks=[Subtask(
        subtask_id="1", description="查资料", agent_type="general_agent",
        skill_name="web_search", input_data={"query": "资料"},
    )])

    result = await scheduler_for({"summary": "找到三条来源", "error": None}).execute(task)

    assert result.status == TaskStatus.COMPLETED
    assert task.subtasks[0].status == TaskStatus.COMPLETED
    assert "找到三条来源" in result.aggregated_output


@pytest.mark.asyncio
async def test_planner_passes_a_task_text_when_model_only_supplies_tool_arguments():
    class Orchestrator:
        def build_catalog_from_registries(self, registry):
            return {"general_agent": {}}

        async def plan(self, user_input, catalog):
            return {"tasks": [{
                "subtask_id": "1", "description": "读取 README.md 并总结",
                "agent": "general_agent", "skill": "read_file",
                "input": {"file_path": "README.md", "action": "read"}, "depends_on": [],
            }]}

    task = await Planner(orchestrator=Orchestrator()).plan("请读取 README.md 并总结主要内容")

    assert task.subtasks[0].input_data["query"] == "读取 README.md 并总结"


@pytest.mark.asyncio
async def test_failed_dependency_does_not_execute_downstream_agent():
    calls = []
    class Agent:
        name = "general_agent"
        _skills = {}
        async def execute(self, context):
            calls.append(context.task_id)
            return {"error": "source unavailable"} if context.task_id == "1" else {"response": "should not run"}
    agent = Agent()
    registry = SimpleNamespace(get=lambda name: agent)
    task = Task(description="两步任务", subtasks=[
        Subtask(subtask_id="1", description="获取来源", agent_type="general_agent", input_data={"query": "a"}),
        Subtask(subtask_id="2", description="根据来源处理", agent_type="general_agent", input_data={"query": "b"}, depends_on=["1"]),
    ])

    result = await Scheduler(registry, enable_checkpoint=False, enable_metrics=False,
                             enable_memory_injection=False, max_retries=0).execute(task)

    assert calls == ["1"]
    assert task.subtasks[1].status == TaskStatus.FAILED
    assert "依赖" in task.subtasks[1].error
    assert result.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_empty_model_plan_falls_back_to_general_worker_with_original_request():
    class Orchestrator:
        def build_catalog_from_registries(self, registry):
            return {"general_agent": {}}

        async def plan(self, user_input, catalog):
            return {"tasks": []}

    task = await Planner(orchestrator=Orchestrator()).plan("整理本地文件")

    assert len(task.subtasks) == 1
    assert task.subtasks[0].agent_type == "general_agent"
    assert task.subtasks[0].input_data["query"] == "整理本地文件"


@pytest.mark.asyncio
async def test_optional_memory_failure_does_not_discard_completed_answer():
    class Memory:
        def recall_context(self, *args, **kwargs):
            return ""
        def store(self, *args, **kwargs):
            raise OSError("memory disk unavailable")
    scheduler = scheduler_for({"response": "任务已完成"})
    scheduler.enable_memory_injection = True
    scheduler._vector_memory = Memory()
    task = Task(description="回答", subtasks=[Subtask(
        subtask_id="1", description="回答", agent_type="general_agent",
        input_data={"query": "回答"},
    )])

    result = await scheduler.execute(task)

    assert result.status == TaskStatus.COMPLETED
    assert "任务已完成" in result.aggregated_output
