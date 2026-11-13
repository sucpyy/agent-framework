"""OfficeAgent — 办公助手 Worker Agent。

架构约束: Worker 不调用其他 Agent，由 Scheduler 统一调度。
"""

from __future__ import annotations
from typing import Any
from .base import Agent, AgentContext, tool_failure_message
from ..sdk.adapter import ClaudeAdapter
from ..skill.office_tools import EmailSkill, DocumentSkill, CalendarSkill, TranslateSkill, ClipboardSkill

OFFICE_PROMPT = """You are an Office Assistant Agent. You help with daily office productivity tasks.

## Your Tools
- **email**: Compose and manage emails
- **document**: Create and edit documents (Markdown, TXT, HTML)
- **calendar**: Manage schedule and events
- **translate**: Translate text between languages
- **clipboard**: Read/write system clipboard

## Rules
1. CRITICAL: ALL responses in Chinese (中文).
2. Professional tone for emails, proper formatting for documents.
3. Be concise and practical.
4. Save email drafts by default; send only when the user explicitly asks you to send.
5. Never claim a document, calendar item, clipboard action, translation, or email was completed unless its tool result confirms success.
6. Focus ONLY on your assigned subtask — do NOT try to delegate to other agents"""


class OfficeAgent(Agent):
    """办公助手 Worker——不调用其他 Agent。"""

    def __init__(self, adapter: ClaudeAdapter | None = None):
        self._email = EmailSkill()
        self._doc = DocumentSkill()
        self._cal = CalendarSkill()
        self._trans = TranslateSkill(adapter)
        self._clip = ClipboardSkill()
        super().__init__(
            name="office_agent",
            skills=[self._email, self._doc, self._cal, self._trans, self._clip],
            system_prompt=OFFICE_PROMPT,
            memory_scope=10,
        )
        self._adapter = adapter or ClaudeAdapter()

    def set_registry(self, registry):
        pass  # Worker 不需要 registry

    def get_all_tool_schemas(self) -> list[dict]:
        return [self._email.to_tool_schema(), self._doc.to_tool_schema(),
                self._cal.to_tool_schema(), self._trans.to_tool_schema(), self._clip.to_tool_schema()]

    def get_all_tool_handlers(self) -> dict:
        return {"email": self._email.execute, "document": self._doc.execute,
                "calendar": self._cal.execute, "translate": self._trans.execute,
                "clipboard": self._clip.execute}

    async def execute(self, context: AgentContext) -> dict:
        msg = context.input_data.get("query", context.input_data.get("description", ""))
        result = await self._adapter.run(
            system_prompt=self.system_prompt,
            messages=[{"role": "user", "content": msg}],
            tools=self.get_all_tool_schemas(),
            tool_handlers=self.get_all_tool_handlers(),
            temperature=0.3, max_tokens=4096, role="office",
        )
        output = {
            "agent": self.name, "task_id": context.task_id, "type": "office_result",
            "response": result["content"],
            "tool_calls": [{"name": tc["name"], "input": tc["input"], "output": tc.get("output", {})}
                           for tc in result.get("tool_calls", [])],
            "usage": result.get("usage", {}), "mode": result.get("mode", "echo"),
        }
        error = tool_failure_message(result.get("tool_calls", []))
        if error:
            output["error"] = error
        return output
