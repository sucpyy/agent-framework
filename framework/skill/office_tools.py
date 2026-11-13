"""办公技能集 — Email / Document / Calendar / Translate / Clipboard"""

from __future__ import annotations
import asyncio, html, json, os, re, shutil, subprocess, sys, time, uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from .base import Skill, SkillContract
from .workspace_paths import display_workspace_path, resolve_workspace_path

# ═══════════════════════════════════════════════════════════
# Email
# ═══════════════════════════════════════════════════════════

EMAIL_SCHEMA = {
    "name": "email",
    "description": "撰写、发送邮件。支持 SMTP 发送，也可生成邮件草稿。",
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "收件人邮箱"},
            "subject": {"type": "string", "description": "邮件主题"},
            "body": {"type": "string", "description": "邮件正文（支持 Markdown）"},
            "action": {"type": "string", "enum": ["send", "draft"], "description": "draft 保存草稿；send 仅在用户明确要求发送且 SMTP 已配置时使用"},
        },
        "required": ["action"],
    },
}

class EmailSkill(Skill):
    def __init__(self):
        super().__init__(SkillContract(
            name="email", capabilities=["email", "send", "compose", "mail"],
            requires=["action"], produces=["status", "preview"],
            cost_score=0.2, latency_score=0.6, quality_score=0.85,
        ))
    def to_tool_schema(self) -> dict: return EMAIL_SCHEMA
    async def execute(self, **kw) -> dict:
        action = kw.get("action", "draft")
        to = kw.get("to", "未指定")
        subject = kw.get("subject", "无主题")
        body = kw.get("body", "")
        if action not in {"draft", "send"}:
            return {"status": "error", "error": "action 必须是 draft 或 send"}
        if not body or not to or "\n" in subject or "\r" in subject:
            return {"status": "error", "error": "收件人、主题和正文均不能为空，主题不能包含换行"}
        from email.message import EmailMessage
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        sender = os.environ.get("SMTP_FROM", "")
        if sender:
            message["From"] = sender
        message.set_content(body)
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            draft_path = resolve_workspace_path(f"drafts/email-{stamp}-{uuid.uuid4().hex[:8]}.eml")
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_bytes(message.as_bytes())
        except Exception as e:
            return {"status": "error", "to": to, "subject": subject, "error": f"草稿保存失败：{e}"}
        if action == "send":
            smtp_host = os.environ.get("SMTP_HOST", "")
            if smtp_host:
                try:
                    import smtplib
                    port = int(os.environ.get("SMTP_PORT", "587"))
                    secure = os.environ.get("SMTP_SSL", "false").lower() in {"1", "true", "yes"}
                    client_type = smtplib.SMTP_SSL if secure else smtplib.SMTP
                    with client_type(smtp_host, port, timeout=20) as client:
                        if not secure and os.environ.get("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"}:
                            client.starttls()
                        user = os.environ.get("SMTP_USER", "")
                        if user:
                            client.login(user, os.environ.get("SMTP_PASS", ""))
                        client.send_message(message)
                    return {"status": "sent", "to": to, "subject": subject, "preview": body[:200], "file_path": display_workspace_path(draft_path)}
                except Exception as e:
                    return {"status": "send_failed", "to": to, "subject": subject, "preview": body[:200], "file_path": display_workspace_path(draft_path), "error": str(e)}
            return {"status": "draft", "to": to, "subject": subject, "preview": body[:200], "file_path": display_workspace_path(draft_path), "note": "SMTP 未配置，草稿已保存。配置 SMTP_HOST、SMTP_PORT、SMTP_FROM 后可发送。"}
        return {"status": "draft", "to": to, "subject": subject, "preview": body[:200], "file_path": display_workspace_path(draft_path)}


# ═══════════════════════════════════════════════════════════
# Document
# ═══════════════════════════════════════════════════════════

DOC_SCHEMA = {
    "name": "document",
    "description": "创建和编辑文档。支持 Markdown、TXT、HTML 格式，可导出。",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "文档标题"},
            "content": {"type": "string", "description": "文档内容（Markdown 格式）"},
            "format": {"type": "string", "enum": ["md", "txt", "html"], "description": "输出格式，默认为 md"},
            "file_path": {"type": "string", "description": "相对工作区根目录或绝对路径；默认为 documents/<安全标题>.<格式>"},
        },
        "required": ["title", "content"],
    },
}

class DocumentSkill(Skill):
    def __init__(self):
        super().__init__(SkillContract(
            name="document", capabilities=["document", "write_doc", "create_doc", "report"],
            requires=["title", "content"], produces=["file_path", "format", "preview"],
            cost_score=0.15, latency_score=0.3, quality_score=0.9,
        ))
    def to_tool_schema(self) -> dict: return DOC_SCHEMA
    async def execute(self, **kw) -> dict:
        title = str(kw.get("title", "untitled")).strip() or "untitled"
        content = str(kw.get("content", ""))
        fmt = str(kw.get("format", "md")).lower().lstrip(".")
        if not content.strip():
            return {"status": "error", "error": "文档内容不能为空"}
        if fmt not in {"md", "txt", "html"}:
            return {"status": "error", "error": "format 仅支持 md、txt、html"}
        safe_title = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", title).strip(".-")[:80] or "untitled"
        try:
            path = resolve_workspace_path(kw.get("file_path"), default=f"documents/{safe_title}.{fmt}")
            path.parent.mkdir(parents=True, exist_ok=True)
            output = content
            if fmt == "html":
                output = ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                          f"<title>{html.escape(title)}</title></head><body><pre>{html.escape(content)}</pre></body></html>")
            path.write_text(output, encoding="utf-8")
            return {"status": "created", "file_path": display_workspace_path(path), "format": fmt, "preview": content[:200], "size": len(output)}
        except Exception as e:
            return {"status": "error", "error": str(e), "file_path": str(kw.get("file_path", ""))}


# ═══════════════════════════════════════════════════════════
# Calendar
# ═══════════════════════════════════════════════════════════

CAL_SCHEMA = {
    "name": "calendar",
    "description": "管理日程。创建、查询、删除日历事件，支持 iCal 导出。",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "delete", "export"], "description": "日历操作"},
            "event_id": {"type": "string", "description": "删除日程时使用的稳定 ID"},
            "file_path": {"type": "string", "description": "导出日历时的文件路径，默认 documents/calendar.ics"},
            "title": {"type": "string", "description": "事件标题"},
            "date": {"type": "string", "description": "日期 (YYYY-MM-DD)"},
            "time": {"type": "string", "description": "时间 (HH:MM)"},
            "duration": {"type": "string", "description": "时长 (如 1h, 30m)"},
            "description": {"type": "string", "description": "事件描述"},
        },
        "required": ["action"],
    },
}

class CalendarSkill(Skill):
    def __init__(self):
        super().__init__(SkillContract(
            name="calendar", capabilities=["calendar", "schedule", "event", "reminder"],
            requires=["action"], produces=["events", "status"],
            cost_score=0.1, latency_score=0.3, quality_score=0.9,
        ))
        self._storage_path = resolve_workspace_path(".workspace_data/calendar_events.json")
        self._events: list[dict] = self._load_events()

    def _load_events(self) -> list[dict]:
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save_events(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._storage_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._events, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._storage_path)
    def to_tool_schema(self) -> dict: return CAL_SCHEMA
    async def execute(self, **kw) -> dict:
        action = kw.get("action", "list")
        if action == "add":
            title, date, event_time = str(kw.get("title", "")).strip(), str(kw.get("date", "")).strip(), str(kw.get("time", "00:00")).strip()
            try:
                if not title: raise ValueError("事件标题不能为空")
                datetime.strptime(date, "%Y-%m-%d")
                datetime.strptime(event_time, "%H:%M")
            except ValueError as e:
                return {"status": "error", "error": str(e) or "日期/时间格式应为 YYYY-MM-DD 和 HH:MM"}
            evt = {"id": uuid.uuid4().hex, "title": title, "date": date, "time": event_time,
                   "duration": kw.get("duration", ""), "description": kw.get("description", "")}
            self._events.append(evt)
            try:self._save_events()
            except OSError as e:
                self._events.pop()
                return {"status": "error", "error": f"日程保存失败：{e}"}
            return {"status": "added", "event_id": evt["id"], "events": self._events[-5:]}
        elif action == "delete":
            event_id, title = kw.get("event_id", ""), kw.get("title", "")
            before = len(self._events)
            self._events = [e for e in self._events if (e.get("id") != event_id if event_id else e.get("title") != title)]
            try:self._save_events()
            except OSError as e:return {"status": "error", "error": f"日程保存失败：{e}"}
            if len(self._events) == before:return {"status": "error", "error": "未找到匹配的日程", "events": self._events}
            return {"status": "deleted", "events": self._events}
        elif action == "export":
            def escape_ical(value):return str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
            events=[]
            for e in self._events:
                start=datetime.strptime(f"{e['date']} {e.get('time','00:00')}", "%Y-%m-%d %H:%M").strftime("%Y%m%dT%H%M%S")
                events.append("BEGIN:VEVENT\nUID:"+e.get("id",uuid.uuid4().hex)+"\nSUMMARY:"+escape_ical(e["title"])+"\nDTSTART:"+start+"\nDESCRIPTION:"+escape_ical(e.get("description",""))+"\nEND:VEVENT")
            ical = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Agent Framework//Calendar//ZH\n" + "\n".join(events) + "\nEND:VCALENDAR"
            try:
                output=resolve_workspace_path(kw.get("file_path"),default="documents/calendar.ics")
                output.parent.mkdir(parents=True,exist_ok=True)
                output.write_text(ical+"\n",encoding="utf-8",newline="\r\n")
                return {"status": "exported", "ical": ical, "file_path":display_workspace_path(output), "count": len(self._events)}
            except OSError as e:return {"status":"error","error":f"导出日历失败：{e}"}
        if action == "list":return {"status": "ok", "events": self._events}
        return {"status": "error", "error": "action 必须是 add、list、delete 或 export"}


# ═══════════════════════════════════════════════════════════
# Translate
# ═══════════════════════════════════════════════════════════

TRANS_SCHEMA = {
    "name": "translate",
    "description": "翻译文本。支持中英日韩法德等主要语言互译。",
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要翻译的文本"},
            "source": {"type": "string", "description": "源语言 (auto 自动检测)"},
            "target": {"type": "string", "description": "目标语言 (zh/en/ja/ko/fr/de)"},
        },
        "required": ["text", "target"],
    },
}

class TranslateSkill(Skill):
    def __init__(self, adapter=None):
        super().__init__(SkillContract(
            name="translate", capabilities=["translate", "language", "i18n"],
            requires=["text", "target"], produces=["translated", "source_lang", "target_lang"],
            cost_score=0.3, latency_score=0.6, quality_score=0.8,
        ))
        self._adapter = adapter
    def to_tool_schema(self) -> dict: return TRANS_SCHEMA
    async def execute(self, **kw) -> dict:
        text = kw.get("text", "")
        target = kw.get("target", "zh")
        source = kw.get("source", "auto")
        if not text.strip():return {"translated":"","source_lang":source,"target_lang":target,"error":"无文本"}
        if not target.strip():return {"translated":"","source_lang":source,"target_lang":target,"error":"目标语言不能为空"}
        if not self._adapter or getattr(self._adapter,"mode","echo") != "live":
            return {"translated":"","source_lang":source,"target_lang":target,"error":"翻译需要已配置的在线模型；当前 Echo 模式不会伪造翻译结果"}
        try:
            result=await self._adapter.run(system_prompt="你是专业翻译。只输出译文，不要解释。保留原文格式、专有名词和代码。",messages=[{"role":"user","content":f"将以下文本从 {source} 翻译为 {target}：\n\n{text}"}],temperature=0.1,max_tokens=4096,role="office")
            translated=str(result.get("content","")).strip()
            if not translated:return {"translated":"","source_lang":source,"target_lang":target,"error":"模型没有返回译文"}
            return {"translated":translated,"source_lang":source,"target_lang":target,"original":text[:200]}
        except Exception as e:return {"translated":"","source_lang":source,"target_lang":target,"error":f"翻译失败：{e}"}


# ═══════════════════════════════════════════════════════════
# Clipboard
# ═══════════════════════════════════════════════════════════

CLIP_SCHEMA = {
    "name": "clipboard",
    "description": "读写剪贴板。支持文本内容的复制和粘贴。",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["read", "write"], "description": "read 读取剪贴板；write 写入剪贴板"},
            "content": {"type": "string", "description": "要写入的内容（write 时必填）"},
        },
        "required": ["action"],
    },
}

class ClipboardSkill(Skill):
    def __init__(self):
        super().__init__(SkillContract(
            name="clipboard", capabilities=["clipboard", "copy", "paste"],
            requires=["action"], produces=["action", "content"],
            cost_score=0.05, latency_score=0.1, quality_score=1.0,
        ))
    def to_tool_schema(self) -> dict: return CLIP_SCHEMA
    async def execute(self, **kw) -> dict:
        action = kw.get("action", "read")
        content = kw.get("content", "")
        if action == "write" and content:
            try:
                import subprocess, sys
                if sys.platform == "win32":
                    result=subprocess.run(["clip.exe"], input=content.encode("utf-16-le"), check=False, timeout=5)
                    if result.returncode:return {"action":"write","status":"error","error":f"clip.exe exit {result.returncode}"}
                elif sys.platform == "darwin":
                    result=subprocess.run(["pbcopy"], input=content.encode(), check=False, timeout=5)
                    if result.returncode:return {"action":"write","status":"error","error":f"pbcopy exit {result.returncode}"}
                else:
                    tool=shutil.which("wl-copy") or shutil.which("xclip") or shutil.which("xsel")
                    if not tool:return {"action":"write","status":"error","error":"未找到 wl-copy、xclip 或 xsel"}
                    args=[tool,"--clipboard"] if tool.endswith("wl-copy") else [tool,"-selection","clipboard"] if tool.endswith("xclip") else [tool,"--clipboard","--input"]
                    result=subprocess.run(args, input=content.encode(), check=False, timeout=5)
                    if result.returncode:return {"action":"write","status":"error","error":f"clipboard command exit {result.returncode}"}
                return {"action": "write", "content": content[:100], "status": "ok"}
            except Exception as e:
                return {"action": "write", "status": f"error: {e}"}
        elif action == "read":
            try:
                import subprocess, sys
                if sys.platform == "win32":
                    shell=shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
                    if not shell:return {"action":"read","content":"","status":"error","error":"未找到 PowerShell"}
                    r = subprocess.run([shell,"-NoProfile","-NonInteractive","-Command","Get-Clipboard -Raw"], capture_output=True, text=True, timeout=5)
                    if r.returncode:return {"action":"read","content":"","status":"error","error":r.stderr.strip() or "读取剪贴板失败"}
                    return {"action": "read", "content": r.stdout[:1000], "status":"ok"}
                elif sys.platform == "darwin":
                    r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
                    return {"action": "read", "content": r.stdout[:1000], "status":"ok" if r.returncode==0 else "error"}
                else:
                    tool=shutil.which("wl-paste") or shutil.which("xclip") or shutil.which("xsel")
                    if not tool:return {"action":"read","content":"","status":"error","error":"未找到 wl-paste、xclip 或 xsel"}
                    args=[tool,"--clipboard"] if tool.endswith("wl-paste") else [tool,"-selection","clipboard","-o"] if tool.endswith("xclip") else [tool,"--clipboard","--output"]
                    r = subprocess.run(args, capture_output=True, text=True, timeout=5)
                    return {"action": "read", "content": r.stdout[:1000], "status":"ok" if r.returncode==0 else "error", **({"error":r.stderr.strip()} if r.returncode else {})}
            except Exception as e:
                return {"action": "read", "content": f"(error: {e})"}
        return {"action": action, "content": "", "status":"error", "error":"action 必须是 read 或 write，且 write 需要 content"}

ALL_OFFICE_TOOLS = [EmailSkill, DocumentSkill, CalendarSkill, TranslateSkill, ClipboardSkill]
