"""CodeSkill — 代码能力。

多个 Anthropic tool schema（read_file, write_file）+ 执行逻辑。
"""

from __future__ import annotations

from typing import Any

from .base import Skill, SkillContract
from .claude_tools import BashSkill, EditSkill, GlobSkill, GrepSkill, ReadSkill, WriteSkill

CODE_CONTRACT = SkillContract(
    name="code",
    capabilities=["code_review", "refactor", "test_gen", "file_ops"],
    requires=["task_description"],
    produces=["changes", "files_modified", "review_notes"],
    cost_score=0.4,
    latency_score=0.5,
    quality_score=0.9,
)


class CodeSkill(Skill):
    """代码技能——读写文件、重构、测试生成。"""

    def __init__(self):
        super().__init__(CODE_CONTRACT)
        self._bash = BashSkill()
        self._read = ReadSkill()
        self._write = WriteSkill()
        self._edit = EditSkill()
        self._glob = GlobSkill()
        self._grep = GrepSkill()
        self._changed_files: list[str] = []

    def begin_task(self) -> None:
        """Clear file-write evidence before starting an independent task."""
        self._changed_files.clear()

    # ── Anthropic Tool Schemas ────────────────

    def to_tool_schemas(self) -> list[dict[str, Any]]:
        """返回多个 tool schema。"""
        return [
            self._bash.to_tool_schema(),
            self._read.to_tool_schema(),
            self._write.to_tool_schema(),
            self._edit.to_tool_schema(),
            self._glob.to_tool_schema(),
            self._grep.to_tool_schema(),
            {
                "name": "apply_changes",
                "description": (
                    "Record a code change. Describes what was changed, which files "
                    "were modified, and provides review notes."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "task_description": {
                            "type": "string",
                            "description": "What code task was performed.",
                        },
                        "changes": {
                            "type": "string",
                            "description": "Description of the changes made.",
                        },
                        "files_modified": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of files modified.",
                        },
                        "review_notes": {
                            "type": "string",
                            "description": "Review notes on the changes.",
                        },
                    },
                    "required": ["task_description", "changes", "files_modified", "review_notes"],
                },
            },
        ]

    # ── Tool Handlers ─────────────────────────

    async def handle_read_file(self, **kwargs: Any) -> dict[str, Any]:
        result=await self._read.execute(**kwargs)
        result["exists"]=result.get("total_lines",0)>0 or result.get("content")=="(empty)"
        return result

    async def handle_write_file(self, **kwargs: Any) -> dict[str, Any]:
        result=await self._write.execute(**kwargs)
        path=result.get("file_path")
        if result.get("written") and path and path not in self._changed_files:self._changed_files.append(path)
        return result

    async def handle_edit_file(self, **kwargs: Any) -> dict[str, Any]:
        result=await self._edit.execute(**kwargs)
        path=result.get("file_path")
        if result.get("modified") and path and path not in self._changed_files:self._changed_files.append(path)
        return result

    async def handle_apply_changes(self, **kwargs: Any) -> dict[str, Any]:
        """记录代码变更——即 CodeSkill 的主执行逻辑。"""
        changes = kwargs.get("changes", "")
        requested = kwargs.get("files_modified", [])
        files = [path for path in requested if path in self._changed_files] if isinstance(requested,list) else []
        if not files:files=list(self._changed_files)
        notes = kwargs.get("review_notes", "")

        if not changes or not files:
            return {"changes": changes, "files_modified": files, "review_notes": notes,
                    "error": "未检测到实际文件变更；请先执行文件工具，再记录变更。"}
        return {
            "changes": changes,
            "files_modified": files,
            "review_notes": notes,
            "recorded": True,
        }

    def get_tool_handlers(self) -> dict[str, Any]:
        return {"bash":self._bash.execute,"read_file":self.handle_read_file,"write_file":self.handle_write_file,
                "edit_file":self.handle_edit_file,"glob":self._glob.execute,
                "grep":self._grep.execute,"apply_changes":self.handle_apply_changes}

    # ── Legacy execute (for non-Claude use) ────

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        if not self.validate_input(**kwargs):
            missing = [k for k in self.contract.requires if k not in kwargs]
            return {
                "changes": "",
                "files_modified": [],
                "review_notes": "",
                "error": f"Missing required inputs: {missing}",
            }

        return {
            "changes": "",
            "files_modified": [],
            "review_notes": "",
            "error": "CodeSkill 只负责能力匹配；请通过 code_agent 执行读写与编辑工具。",
        }
