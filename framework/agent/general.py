"""GeneralAgent — 拥有全部 Claude Code 工具集的通用 Agent。

等同于 Claude Code 的主 Agent：Bash / Read / Write / Edit / Glob / Grep / WebSearch / WebFetch / TodoWrite。
"""

from __future__ import annotations

from typing import Any

from .base import Agent, AgentContext, tool_failure_message
from ..sdk.adapter import ClaudeAdapter
from ..skill.claude_tools import (
    BashSkill, ReadSkill, WriteSkill, EditSkill,
    GlobSkill, GrepSkill, TodoWriteSkill, SpawnAgentSkill,
)
from ..skill.web_search import WebSearchSkill

GENERAL_PROMPT = """You are a General Agent with full system access — equivalent to Claude Code.

## Your Capabilities
You have access to ALL system tools:
- **bash**: Execute terminal commands from the project root. Windows uses Git Bash when installed, otherwise PowerShell.
- **read_file**: Read any file with line numbers
- **write_file**: Create or overwrite files
- **edit_file**: Precise string replacement in files
- **glob**: Find files by pattern (e.g., **/*.py)
- **grep**: Search file contents with regex
- **web_search**: Search the internet
- **search_and_fetch**: Search the web and fetch result page content
- **todo_write**: Track complex multi-step tasks

## Rules
1. CRITICAL: ALL responses MUST be in Chinese (中文). Code/comments can be English, but all explanations in Chinese.
2. Resolve every relative file path from the project workspace root, regardless of the process working directory.
3. Before modifying files, always read_file first to understand current state.
4. Use todo_write for tasks with 3+ steps or 2+ files.
5. Prefer edit_file over write_file for existing files.
6. Never claim a file was changed unless a write_file or edit_file result confirms success.
7. After making changes, verify they work (run tests, check syntax).
8. If a command fails, diagnose before retrying.
9. Be thorough — consider edge cases, error handling, and tests.

## Code Quality
- Match existing code style in the project
- Use clear variable/function names
- Add comments explaining WHY, not WHAT
- Cover new code with tests"""


class GeneralAgent(Agent):
    """通用 Agent — 拥有全部 Claude Code 工具。"""

    def __init__(self, adapter: ClaudeAdapter | None = None):
        self._bash = BashSkill()
        self._read = ReadSkill()
        self._write = WriteSkill()
        self._edit = EditSkill()
        self._glob = GlobSkill()
        self._grep = GrepSkill()
        self._websearch = WebSearchSkill()
        self._todo = TodoWriteSkill()
        self._spawn = SpawnAgentSkill()

        all_skills = [
            self._bash, self._read, self._write, self._edit,
            self._glob, self._grep, self._websearch, self._todo,
            self._spawn,
        ]

        super().__init__(
            name="general_agent",
            skills=all_skills,
            system_prompt=GENERAL_PROMPT,
            memory_scope=20,
        )
        self._adapter = adapter or ClaudeAdapter()

    # ── Tool Schemas (for LLM) ────────────────

    def set_registry(self, registry):
        """注入 AgentRegistry，使 spawn_agent 可用。"""
        self._spawn._registry = registry

    def get_all_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            self._bash.to_tool_schema(),
            self._read.to_tool_schema(),
            self._write.to_tool_schema(),
            self._edit.to_tool_schema(),
            self._glob.to_tool_schema(),
            self._grep.to_tool_schema(),
            *self._websearch.to_tool_schemas(),  # web_search + search_and_fetch
            self._todo.to_tool_schema(),
            self._spawn.to_tool_schema(),
        ]

    def get_all_tool_handlers(self) -> dict[str, Any]:
        return {
            "bash": self._bash.execute,
            "read_file": self._read.execute,
            "write_file": self._write.execute,
            "edit_file": self._edit.execute,
            "glob": self._glob.execute,
            "grep": self._grep.execute,
            "web_search": self._websearch.execute,
            "search_and_fetch": self._websearch.execute_fetch,
            "todo_write": self._todo.execute,
            "spawn_agent": self._spawn.execute,
        }

    # ── Execute ───────────────────────────────

    async def execute(self, context: AgentContext) -> dict[str, Any]:
        user_msg = context.input_data.get("query", context.input_data.get("description", ""))
        target = context.input_data.get("target_file", "")

        prompt = user_msg
        if target:
            prompt = f"Task: {user_msg}\nTarget: {target}"

        result = await self._adapter.run(
            system_prompt=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            tools=self.get_all_tool_schemas(),
            tool_handlers=self.get_all_tool_handlers(),
            temperature=0.3,
            max_tokens=4096,
            role="general",
        )

        output = {
            "agent": self.name,
            "task_id": context.task_id,
            "type": "general_result",
            "response": result["content"],
            "tool_calls": [
                {"name": tc["name"], "input": tc["input"], "output": tc.get("output", {})}
                for tc in result.get("tool_calls", [])
            ],
            "usage": result.get("usage", {}),
            "mode": result.get("mode", "echo"),
        }
        error = tool_failure_message(result.get("tool_calls", []))
        if error:
            output["error"] = error
        return output
