"""CodeAgent — 编码型 Worker Agent。

架构约束: Worker 不调用其他 Agent，由 Scheduler 统一调度。
"""

from __future__ import annotations
from typing import Any
from .base import Agent, AgentContext
from ..skill.code import CodeSkill
from ..sdk.adapter import ClaudeAdapter

CODING_PROMPT = """You are a Code Agent. Your job is to write, review, and refactor code.

## Your Tools
- **bash, read_file, write_file, edit_file, glob, grep**: Run checks and operate on files
- **apply_changes**: Record your code changes

## Rules
1. Read file before modifying it
2. Plan minimal changes
3. CRITICAL: ALL explanations in Chinese (中文). Code can be English.
4. Always use apply_changes to record what you did.
5. Focus ONLY on your assigned subtask — do NOT try to delegate to other agents"""


class CodeAgent(Agent):
    """编码型 Worker——仅持有 CodeSkill，不调用其他 Agent。"""

    def __init__(self, adapter: ClaudeAdapter | None = None):
        self._code_skill = CodeSkill()
        super().__init__(
            name="code_agent",
            skills=[self._code_skill],
            system_prompt=CODING_PROMPT,
            memory_scope=10,
        )
        self._adapter = adapter or ClaudeAdapter()

    def set_registry(self, registry):
        pass  # Worker 不需要 registry

    def get_all_tool_schemas(self) -> list[dict]:
        return self._code_skill.to_tool_schemas()

    def get_all_tool_handlers(self) -> dict:
        return self._code_skill.get_tool_handlers()

    async def execute(self, context: AgentContext) -> dict[str, Any]:
        self._code_skill.begin_task()
        task_desc = context.input_data.get("task_description", context.input_data.get("description", ""))
        target_file = context.input_data.get("target_file", "")
        result = await self._adapter.run(
            system_prompt=self.system_prompt,
            messages=[{"role": "user", "content": f"Code task: {task_desc}\nTarget: {target_file}"}],
            tools=self.get_all_tool_schemas(),
            tool_handlers=self.get_all_tool_handlers(),
        )
        changes_output = {}
        actual_files = []
        for tc in result.get("tool_calls", []):
            if tc["name"] == "apply_changes" and "output" in tc:
                changes_output = tc["output"]
            if tc["name"] == "write_file" and tc.get("output", {}).get("written"):
                actual_files.append(tc["output"].get("file_path", ""))
            if tc["name"] == "edit_file" and tc.get("output", {}).get("modified"):
                actual_files.append(tc["output"].get("file_path", ""))
        files_modified = changes_output.get("files_modified") or [path for path in actual_files if path]
        changes = changes_output.get("changes") or (result.get("content", "") if files_modified else "未执行文件写入或修改。")
        requires_change = any(word in task_desc.lower() for word in (
            "修复", "实现", "创建", "新增", "重构", "修改", "改写", "写入", "编写", "写一个", "开发", "搭建",
            "build", "implement", "fix", "refactor",
        ))
        error = changes_output.get("error") or (
            "任务要求修改代码，但没有任何文件写入或编辑成功。" if requires_change and not files_modified else None
        )
        output = {
            "agent": self.name, "task_id": context.task_id,
            "type": "code_result",
            "changes": changes,
            "files_modified": files_modified,
            "review_notes": changes_output.get("review_notes", ""),
            "claude_response": result["content"],
            "usage": result["usage"], "mode": result["mode"],
        }
        if error:
            output["error"] = error
        return output
