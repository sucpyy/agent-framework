"""Orchestrator Agent — 计划者，不是管理者。

拿到用户输入 → 推理 → 输出结构化计划 JSON → 退出舞台。

不参与执行、不监控、不裁决、不重试。
Agent 选择采用 Option A：Orchestrator 在计划中指定每个子任务的 Agent。
"""

from __future__ import annotations

from typing import Any

from ..sdk.adapter import ClaudeAdapter

ORCHESTRATOR_PROMPT = """You are an Orchestrator Agent. Your job is to analyze user requests and break them down into structured task plans.

IMPORTANT: All your responses MUST be in Chinese (中文). This includes task descriptions, input data, and any text you generate.

## Your role
You are a PLANNER, not a manager. You output a plan and exit. You do NOT monitor execution, retry tasks, or judge results.

## Available agents
{agent_catalog}

## Output format
You MUST call the create_plan tool with exactly this structure. Do NOT output text before or after the tool call.

Rules:
1. Each subtask gets a unique subtask_id ("1", "2", "3", ...)
2. Assign each subtask to the most appropriate agent
3. Specify which skill of that agent to use
4. Extract precise input data from the user's request
5. Declare dependencies: if subtask 3 needs results from subtask 1 and 2, set depends_on: ["1", "2"]
6. Independent tasks should have empty depends_on: []
7. If the user's request can be handled by a single agent, output ONE subtask
8. DO NOT create unnecessary subtasks — only decompose when it adds value"""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "description": "Ordered list of subtasks to execute",
            "items": {
                "type": "object",
                "properties": {
                    "subtask_id": {
                        "type": "string",
                        "description": "Unique ID for this subtask (e.g. '1', '2')",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description of what to do",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Agent name from the catalog to assign this task to",
                    },
                    "skill": {
                        "type": "string",
                        "description": "Specific skill the agent should use",
                    },
                    "input": {
                        "type": "object",
                        "description": "Input data for the agent. Must include the required fields for the skill.",
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of subtask_ids this task depends on",
                    },
                },
                "required": ["subtask_id", "description", "agent", "skill", "input", "depends_on"],
            },
        },
        "dependencies": {
            "type": "array",
            "description": "Dependency edges between tasks",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "Source subtask_id"},
                    "to": {"type": "string", "description": "Target subtask_id that depends on source"},
                },
                "required": ["from", "to"],
            },
        },
    },
    "required": ["tasks", "dependencies"],
}


class OrchestratorAgent:
    """Orchestrator — 用 Claude SDK 做任务拆解和 Agent 选择。

    只做一次推理：输入 → 计划 JSON → 退出。
    """

    def __init__(self, adapter: ClaudeAdapter | None = None):
        self._adapter = adapter or ClaudeAdapter()

    def _build_system_prompt(self, agent_catalog: dict[str, Any]) -> str:
        """构建包含 Agent 目录的系统提示。"""
        catalog_lines = []
        for name, info in agent_catalog.items():
            skills = ", ".join(info.get("skills", []))
            capabilities = ", ".join(info.get("capabilities", []))
            catalog_lines.append(
                f"  - **{name}**: skills=[{skills}], capabilities=[{capabilities}]"
            )

        return ORCHESTRATOR_PROMPT.format(
            agent_catalog="\n".join(catalog_lines)
        )

    def build_catalog_from_registries(
        self,
        agent_registry: Any,  # AgentRegistry
    ) -> dict[str, Any]:
        """从 AgentRegistry 构建 catalog。"""
        catalog: dict[str, Any] = {}
        for agent_name in agent_registry.list_all():
            agent = agent_registry.get(agent_name)
            if agent is None:
                continue
            # 收集该 Agent 所有 Skill 的能力标签
            capabilities: list[str] = []
            for skill in agent._skills.values():
                capabilities.extend(skill.contract.capabilities)
            catalog[agent_name] = {
                "skills": agent.skill_names,
                "capabilities": list(set(capabilities)),
            }
        return catalog

    async def plan(
        self,
        user_input: str,
        agent_catalog: dict[str, Any],
    ) -> dict[str, Any]:
        """执行计划拆解。

        Returns:
            {"tasks": [...], "dependencies": [...]}
        """
        # Echo 模式：用启发式规则生成计划（模拟 LLM 行为）
        if self._adapter.mode == "echo":
            return self._plan_echo(user_input, agent_catalog)

        # Live 模式：真实 Claude API 调用
        system_prompt = self._build_system_prompt(agent_catalog)

        result = await self._adapter.run_structured(
            system_prompt=system_prompt,
            messages=[
                {"role": "user", "content": f"Plan the following request:\n\n{user_input}"}
            ],
            output_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Always set to 'create_plan'",
                        "const": "create_plan",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of the overall plan",
                    },
                    "input_schema": PLAN_SCHEMA,
                },
                "required": ["name", "input_schema"],
            },
        )

        # 提取 plan 数据
        if "input_schema" in result:
            plan = result["input_schema"]
        elif "tasks" in result:
            plan = result
        else:
            plan = {"tasks": [], "dependencies": [], "error": "Failed to parse plan"}

        return plan

    def _plan_echo(
        self,
        user_input: str,
        agent_catalog: dict[str, Any],
    ) -> dict[str, Any]:
        """Echo 模式下用启发式规则生成计划。

        优先路由到 general_agent（拥有全部工具），特定场景用专门 Agent。
        """
        agent_names = list(agent_catalog.keys())
        tasks = []
        task_id = 0

        # GeneralAgent 优先 — 它能处理几乎所有任务
        has_general = "general_agent" in agent_names

        # 特定场景检测
        needs_pure_search = any(
            kw in user_input.lower()
            for kw in ["搜索", "查", "调研", "比较", "对比", "search",
                        "research", "compare", "什么是", "最新"]
        ) and not any(kw in user_input.lower() for kw in [
            "写", "重构", "修复", "改", "实现", "code", "refactor", "fix",
            "implement", "build", "模块", "函数", "jwt", "auth", "数据库",
            "运行", "执行", "安装", "配置", "创建", "删除", "移动", "复制",
        ])

        needs_pure_code = any(
            kw in user_input.lower()
            for kw in ["代码", "写", "重构", "修复", "改", "实现", "code",
                        "refactor", "fix", "implement", "build", "模块",
                        "函数", "jwt", "auth", "数据库"]
        ) and not any(kw in user_input.lower() for kw in [
            "搜索", "查", "调研", "比较", "对比", "search", "research",
            "运行", "执行", "cli", "bash", "终端",
        ])

        needs_cli = any(
            kw in user_input.lower()
            for kw in ["运行", "执行", "安装", "配置", "删除", "移动",
                        "复制", "启动", "停止", "重启", "测试", "编译",
                        "打包", "发布", "部署", "git", "npm", "pip",
                        "docker", "build", "run", "test", "deploy"]
        )

        office_skill = None
        office_routes = [
            ("email", ("邮件", "email", "发信", "写信")),
            ("document", ("文档", "报告", "word", "markdown 文件")),
            ("calendar", ("日程", "日历", "calendar", "提醒")),
            ("translate", ("翻译", "translate", "译成")),
            ("clipboard", ("剪贴板", "clipboard", "复制到剪贴板", "读取剪贴板")),
        ]
        lowered = user_input.lower()
        for skill_name, keywords in office_routes:
            if any(keyword in lowered for keyword in keywords):
                office_skill = skill_name
                break

        # 综合任务 → general_agent
        needs_mixed = not needs_pure_search and not needs_pure_code and not needs_cli

        if office_skill and "office_agent" in agent_names:
            tasks.append({
                "subtask_id": "1",
                "description": user_input[:80],
                "agent": "office_agent",
                "skill": office_skill,
                "input": {"query": user_input},
                "depends_on": [],
            })
        elif has_general and (needs_mixed or needs_cli):
            task_id += 1
            tasks.append({
                "subtask_id": str(task_id),
                "description": user_input[:80],
                "agent": "general_agent",
                "skill": "bash",
                "input": {"query": user_input},
                "depends_on": [],
            })
        else:
            # 纯搜索 → research_agent
            if needs_pure_search and "research_agent" in agent_names:
                task_id += 1
                query = self._extract_search_query(user_input)
                tasks.append({
                    "subtask_id": str(task_id),
                    "description": f"搜索: {query}",
                    "agent": "research_agent",
                    "skill": "search",
                    "input": {"query": query},
                    "depends_on": [],
                })

            # 纯代码 → code_agent
            if needs_pure_code and "code_agent" in agent_names:
                task_id += 1
                code_task = self._extract_code_task(user_input)
                target = self._extract_target_file(user_input)
                tasks.append({
                    "subtask_id": str(task_id),
                    "description": f"代码: {code_task}",
                    "agent": "code_agent",
                    "skill": "code",
                    "input": {"task_description": code_task, "target_file": target},
                    "depends_on": [],
                })

        # Fallback
        if not tasks and has_general:
            task_id += 1
            tasks.append({
                "subtask_id": str(task_id),
                "description": user_input[:80],
                "agent": "general_agent",
                "skill": "bash",
                "input": {"query": user_input},
                "depends_on": [],
            })

        return {"tasks": tasks, "dependencies": []}

    def _extract_search_query(self, text: str) -> str:
        keywords = ["搜索", "调研", "比较", "对比"]
        for kw in keywords:
            if kw in text:
                return text[text.index(kw):].strip()
        return text.strip()

    def _extract_code_task(self, text: str) -> str:
        keywords = ["重构", "修复", "写", "实现", "改"]
        for kw in keywords:
            if kw in text:
                return text[text.index(kw):].strip()
        return text.strip()

    def _extract_target_file(self, text: str) -> str:
        import re
        match = re.search(r'[\w/]+\.(py|ts|js|tsx|jsx)', text)
        if match:
            return match.group(0)
        return "main.py"
