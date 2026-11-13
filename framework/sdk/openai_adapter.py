"""OpenAI-compatible Adapter — DeepSeek / 本地代理 / 任意 OpenAI 兼容后端。

支持:
  - DeepSeek API (api.deepseek.com)
  - 本地代理 (localhost:8000/v1)
  - 任意 OpenAI-compatible 端点

接口与 ClaudeAdapter 完全一致 (run / run_structured)，
Agent 层无需修改即可切换后端。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

_OPENAI_AVAILABLE = False
try:
    from openai import AsyncOpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    pass


class RateLimiter:
    """令牌桶速率限制器（与 ClaudeAdapter 共享同一实现）。"""

    def __init__(self, rate: float = 5.0, burst: int = 10):
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


class OpenAIAdapter:
    """OpenAI 兼容适配器——对接 DeepSeek / 代理 / 任意兼容端点。

    用法:
        # DeepSeek 直连
        adapter = OpenAIAdapter(
            api_key="sk-...",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        )

        # 本地代理
        adapter = OpenAIAdapter(
            api_key="not-needed",
            base_url="http://localhost:8000/v1",
            model="deepseek-chat",
        )
    """

    DEFAULT_MODELS = {
        "orchestrator": "deepseek-chat",
        "research": "deepseek-chat",
        "code": "deepseek-chat",
        "default": "deepseek-chat",
    }

    def __init__(
        self,
        model: str = "deepseek-chat",
        api_key: str | None = None,
        base_url: str = "https://api.deepseek.com/v1",
        max_tool_rounds: int = 10,
        rate_limit: float = 5.0,
        rate_burst: int = 10,
    ):
        self.model = model
        self.api_key = (
            api_key
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        self.max_tool_rounds = max_tool_rounds
        self._live = _OPENAI_AVAILABLE and bool(self.api_key)
        self._rate_limiter = RateLimiter(rate=rate_limit, burst=rate_burst)
        self._models = dict(self.DEFAULT_MODELS)

    @property
    def mode(self) -> str:
        return "live" if self._live else "echo"

    @property
    def backend(self) -> str:
        """返回后端标识。"""
        if "deepseek" in self.base_url:
            return "deepseek"
        if "localhost" in self.base_url or "127.0.0.1" in self.base_url:
            return "local-proxy"
        return "openai-compatible"

    # ── Multi-model Routing ───────────────────

    def set_model_for_role(self, role: str, model: str) -> None:
        self._models[role] = model

    def get_model_for_role(self, role: str) -> str:
        return self._models.get(role, self.model)

    # ── Public API ────────────────────────────

    async def run(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_handlers: dict[str, Any] | None = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        role: str = "default",
    ) -> dict[str, Any]:
        """运行 Agent 循环。"""
        model = self.get_model_for_role(role)
        openai_tools = self._to_openai_tools(tools) if tools else None

        if self._live:
            await self._rate_limiter.acquire()
            return await self._run_live(
                system_prompt, messages, openai_tools, tool_handlers,
                temperature, max_tokens, model,
            )
        else:
            return await self._run_echo(
                system_prompt, messages, tools, tool_handlers,
            )

    async def run_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        output_schema: dict[str, Any],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        role: str = "orchestrator",
    ) -> dict[str, Any]:
        """运行并要求结构化 JSON 输出。"""
        tool_schema = {
            "name": "output_structured",
            "description": "Emit the final structured output.",
            "input_schema": output_schema,
        }

        async def _handler(**kwargs: Any) -> dict[str, Any]:
            return kwargs

        result = await self.run(
            system_prompt=system_prompt,
            messages=messages,
            tools=[tool_schema],
            tool_handlers={"output_structured": _handler},
            temperature=temperature,
            max_tokens=max_tokens,
            role=role,
        )

        if result["tool_calls"]:
            return result["tool_calls"][-1]["input"]
        try:
            return json.loads(result["content"])
        except json.JSONDecodeError:
            return {"raw": result["content"]}

    async def run_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_handlers: dict[str, Any] | None = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        role: str = "default",
    ):
        """流式运行 Agent 循环，yield 事件 dict。

        Events:
            {"type": "text", "content": "..."}
            {"type": "tool_start", "name": "...", "input": {...}}
            {"type": "tool_end", "name": "...", "output": {...}}
            {"type": "done", "usage": {...}, "model": "..."}
        """
        model = self.get_model_for_role(role)

        if not self._live:
            # Echo fallback
            result = await self._run_echo(system_prompt, messages, tools, tool_handlers)
            for word in result["content"].split():
                yield {"type": "text", "content": word + " "}
                await asyncio.sleep(0.01)
            for tc in result.get("tool_calls", []):
                yield {"type": "tool_start", "name": tc["name"], "input": tc.get("input", {})}
                yield {"type": "tool_end", "name": tc["name"], "output": tc.get("output", {})}
            yield {"type": "done", "usage": result["usage"], "model": result["model"]}
            return

        await self._rate_limiter.acquire()
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url,
                             timeout=30.0, max_retries=0)

        openai_messages: list[dict[str, Any]] = []
        if system_prompt:
            openai_messages.append({"role": "system", "content": system_prompt})
        openai_messages.extend(self._to_openai_messages(messages))

        total_input = 0
        total_output = 0

        for _round in range(self.max_tool_rounds):
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": openai_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                kwargs["tools"] = self._to_openai_tools(tools)
                kwargs["tool_choice"] = "auto"

            stream = await client.chat.completions.create(**kwargs)

            content_chunks: list[str] = []
            tool_call_chunks: dict[int, dict] = {}
            usage = {}

            async for chunk in stream:
                if chunk.usage:
                    usage = {"input_tokens": chunk.usage.prompt_tokens or 0,
                             "output_tokens": chunk.usage.completion_tokens or 0}

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                if delta.content:
                    content_chunks.append(delta.content)
                    yield {"type": "text", "content": delta.content}

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_call_chunks:
                            tool_call_chunks[idx] = {"id": "", "name": "", "arguments": ""}
                        # DeepSeek may send id in first chunk OR only in later chunks
                        if tc.id:
                            tool_call_chunks[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_call_chunks[idx]["name"] += tc.function.name
                            if tc.function.arguments:
                                tool_call_chunks[idx]["arguments"] += tc.function.arguments

            # Fallback: generate IDs for any tool calls missing them (DeepSeek quirk)
            for idx, tc in tool_call_chunks.items():
                if not tc["id"]:
                    tc["id"] = f"call_{uuid.uuid4().hex[:12]}"

            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)

            full_text = "".join(content_chunks)

            # No tool calls → done
            if not tool_call_chunks:
                yield {"type": "done", "usage": usage, "model": model, "mode": "live", "backend": self.backend}
                return

            # Process tool calls — execute handlers first
            sorted_tools = sorted(tool_call_chunks.items(), key=lambda x: x[0])
            tool_outputs = []

            for idx, tc_data in sorted_tools:
                tool_name = tc_data["name"]
                try:
                    tool_input = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                except json.JSONDecodeError:
                    tool_input = {}

                yield {"type": "tool_start", "name": tool_name, "input": tool_input}

                # 安全执行: 单个工具失败不影响整体循环
                try:
                    handler = (tool_handlers or {}).get(tool_name)
                    if handler:
                        if asyncio.iscoroutinefunction(handler):
                            output = await handler(**tool_input)
                        else:
                            output = handler(**tool_input)
                        # 结果为 None → 视为空
                        if output is None:
                            output = {"result": "ok"}
                    else:
                        output = {"error": f"No handler for '{tool_name}'"}
                except Exception as tool_err:
                    output = {"error": f"Tool '{tool_name}' failed: {str(tool_err)}"}

                yield {"type": "tool_end", "name": tool_name, "output": output}
                tool_outputs.append({"id": tc_data["id"], "name": tool_name, "output": output})

            # Append assistant message with tool_calls
            openai_messages.append({
                "role": "assistant",
                "content": full_text or None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for _, tc in sorted_tools
                ],
            })
            # Append individual tool result messages
            for to in tool_outputs:
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": to["id"],
                    "content": json.dumps(to["output"], ensure_ascii=False),
                })

        yield {"type": "done", "usage": {"input_tokens": total_input, "output_tokens": total_output}, "model": model, "mode": "live", "backend": self.backend}

    # ── Live Mode (OpenAI SDK) ─────────────────

    async def _run_live(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_handlers: dict[str, Any] | None,
        temperature: float,
        max_tokens: int,
        model: str,
    ) -> dict[str, Any]:
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url,
                             timeout=30.0, max_retries=0)
        tool_calls_log: list[dict[str, Any]] = []
        total_input = 0
        total_output = 0

        # 构建 OpenAI 格式消息
        openai_messages: list[dict[str, Any]] = []
        if system_prompt:
            openai_messages.append({"role": "system", "content": system_prompt})
        openai_messages.extend(self._to_openai_messages(messages))

        for _round in range(self.max_tool_rounds):
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": openai_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = await client.chat.completions.create(**kwargs)

            choice = response.choices[0]
            total_input += response.usage.prompt_tokens if response.usage else 0
            total_output += response.usage.completion_tokens if response.usage else 0

            msg = choice.message

            # 检查是否有 tool_calls
            if msg.tool_calls:
                # 将 assistant 消息加入历史
                openai_messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })

                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_input = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_input = {}

                    try:
                        handler = (tool_handlers or {}).get(tool_name)
                        if handler:
                            if asyncio.iscoroutinefunction(handler):
                                output = await handler(**tool_input)
                            else:
                                output = handler(**tool_input)
                            if output is None:
                                output = {"result": "ok"}
                        else:
                            output = {"error": f"No handler for tool '{tool_name}'"}
                    except Exception as tool_err:
                        output = {"error": f"Tool '{tool_name}' failed: {str(tool_err)}"}

                    tool_calls_log.append({
                        "name": tool_name,
                        "input": tool_input,
                        "output": output,
                    })

                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(output, ensure_ascii=False),
                    })
            else:
                # 纯文本响应 → 结束
                return {
                    "content": msg.content or "",
                    "tool_calls": tool_calls_log,
                    "usage": {
                        "input_tokens": total_input,
                        "output_tokens": total_output,
                    },
                    "model": response.model,
                    "mode": "live",
                    "backend": self.backend,
                }

        return {
            "content": "[max tool rounds exceeded]",
            "tool_calls": tool_calls_log,
            "usage": {"input_tokens": total_input, "output_tokens": total_output},
            "model": model,
            "mode": "live",
            "backend": self.backend,
        }

    # ── Echo Mode ──────────────────────────────

    async def _run_echo(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_handlers: dict[str, Any] | None,
    ) -> dict[str, Any]:
        tool_calls: list[dict[str, Any]] = []
        user_content = messages[-1]["content"] if messages else ""

        selected = self._echo_tool_name(user_content)
        if tools and tool_handlers and selected:
            for tool in tools:
                tool_name = tool["name"]
                if tool_name != selected:
                    continue
                handler = tool_handlers.get(tool_name)
                if handler is None:
                    continue
                tool_input = self._echo_extract_input(tool, user_content, system_prompt)
                if asyncio.iscoroutinefunction(handler):
                    output = await handler(**tool_input)
                else:
                    output = handler(**tool_input)
                tool_calls.append({
                    "name": tool_name,
                    "input": tool_input,
                    "output": output,
                })

        if tool_calls:
            content = self._echo_synthesize(system_prompt, user_content, tool_calls)
        else:
            content = "当前未连接可用模型，无法根据自然语言自动执行此任务。请在系统设置中检查模型连接。"

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": {
                "input_tokens": len(system_prompt) // 4,
                "output_tokens": len(content) // 4,
            },
            "model": f"{self.model} (echo)",
            "mode": "echo",
            "backend": self.backend,
        }

    @staticmethod
    def _echo_tool_name(message: str) -> str | None:
        """Run only an explicitly requested tool in Echo mode."""
        text = str(message).lower()
        choices = (
            ("search_and_fetch", ("search_and_fetch", "搜索并抓取", "搜索并打开")),
            ("web_search", ("web_search", "搜索", "查找网页", "search the web")),
            ("read_file", ("read_file", "读取文件")),
            ("write_file", ("write_file", "写入文件")),
            ("edit_file", ("edit_file", "编辑文件")),
            ("glob", ("glob", "查找文件")),
            ("grep", ("grep", "搜索文件内容")),
            ("bash", ("bash", "运行命令", "执行命令")),
            ("email", ("email", "邮件")),
            ("document", ("document", "创建文档")),
            ("calendar", ("calendar", "日历", "日程")),
            ("translate", ("translate", "翻译")),
            ("clipboard", ("clipboard", "剪贴板")),
            ("todo_write", ("todo_write", "待办")),
            ("spawn_agent", ("spawn_agent",)),
        )
        return next((name for name, keywords in choices if any(word in text for word in keywords)), None)

    def _echo_extract_input(
        self, tool: dict[str, Any], user_content: str, system_prompt: str
    ) -> dict[str, Any]:
        import re as _re
        schema = tool.get("input_schema", {})
        properties = schema.get("properties", {})
        result: dict[str, Any] = {}
        uc = user_content if isinstance(user_content, str) else str(user_content)
        for prop_name, prop_schema in properties.items():
            prop_type = prop_schema.get("type", "string")
            if prop_type == "string":
                if prop_name in ("query", "description", "task_description", "text", "body", "content"):
                    result[prop_name] = uc[:2000]
                elif prop_name in ("subject", "title"):
                    m = _re.search(r'(?:主题|标题|关于)[：:]\s*(.+?)(?:[，,。\n]|$)', uc)
                    result[prop_name] = m.group(1).strip()[:100] if m else uc[:80]
                elif prop_name in ("to", "recipient"):
                    # 先尝试匹配邮箱地址
                    m = _re.search(r'(\S+@\S+\.\S+)', uc)
                    if m:
                        result[prop_name] = m.group(1).strip()[:100]
                    else:
                        m = _re.search(r'(?:收件人|发送邮件给|发给|发送给|to)[：:\s]*(\S+)', uc, _re.I)
                        result[prop_name] = m.group(1).strip()[:100] if m else "未指定"
                elif prop_name in ("date", "time"):
                    m = _re.search(r'(\d{4}-\d{2}-\d{2}|\d{2}:\d{2})', uc)
                    result[prop_name] = m.group(1) if m else ""
                elif prop_name in ("target", "source"):
                    m = _re.search(r'(?:目标语言|源语言|翻译成|翻译为|译成|译为|from|to)[：:]?\s*(\w{2,3})', uc, _re.I)
                    result[prop_name] = m.group(1) if m else ("zh" if prop_name=="target" else "auto")
                elif prop_name == "action":
                    for kw, val in [("send","send"),("draft","draft"),("list","list"),("add","add"),
                                   ("delete","delete"),("export","export"),("read","read"),("write","write")]:
                        if kw in uc.lower(): result[prop_name] = val; break
                    if prop_name not in result: result[prop_name] = ""
                elif prop_name == "format":
                    for kw, val in [("md","md"),("txt","txt"),("html","html"),("markdown","md")]:
                        if kw in uc.lower(): result[prop_name] = val; break
                    if prop_name not in result: result[prop_name] = "md"
                elif "file" in prop_name.lower() or "path" in prop_name.lower():
                    m = _re.search(r'([\w./\\-]+\.\w{1,4})', uc)
                    if m:
                        result[prop_name] = m.group(1)
                    elif prop_name == "file_path":
                        # No valid path in input → generate default
                        title_m = _re.search(r'(?:标题|title)[：:]\s*(.+?)(?:[，,。\n]|$)', uc)
                        title = (title_m.group(1).strip() if title_m else "output").replace(' ','_')[:30]
                        fmt_m = _re.search(r'(?:格式|format)[：:]\s*(\w+)', uc)
                        fmt = fmt_m.group(1) if fmt_m else "md"
                        result[prop_name] = f"output/{title}.{fmt}"
                    else:
                        result[prop_name] = "output.txt"
                elif prop_name == "duration":
                    m = _re.search(r'(\d+\s*[hm分钟小时])', uc)
                    result[prop_name] = m.group(1) if m else "1h"
                else:
                    result[prop_name] = uc[:300]
            elif prop_type == "array":
                result[prop_name] = []
            elif prop_type == "object":
                result[prop_name] = {}
            elif prop_type in ("integer", "number"):
                m = _re.search(r'(\d+)', uc)
                result[prop_name] = int(m.group(1)) if m else (prop_schema.get("default", 1) if isinstance(prop_schema.get("default"),(int,float)) else 1)
            elif prop_type == "boolean":
                result[prop_name] = any(kw in uc.lower() for kw in ("true", "yes", "是", "启用"))
        return result

    def _echo_synthesize(
        self, system_prompt: str, user_content: str, tool_calls: list[dict[str, Any]]
    ) -> str:
        lines = []
        for tc in tool_calls:
            name = tc["name"]
            output = tc.get("output", {})
            if not isinstance(output, dict):
                lines.append(f"[{name}] {str(output)[:300]}")
                continue
            if output.get("error") or output.get("status") in {"error", "send_failed"}:
                lines.append(f"工具 {name} 执行失败：{output.get('error') or output.get('status')}")
                continue
            # 按工具类型智能格式化输出
            if name == "email":
                verb="邮件已发送" if output.get("status")=="sent" else "邮件草稿已保存"
                lines.append(f"{verb}：收件人 {output.get('to','—')}，主题：{output.get('subject','—')}，位置：{output.get('file_path','—')}")
                lines.append(f"正文预览：{output.get('preview','')[:200]}")
            elif name == "document":
                lines.append(f"文档已创建：{output.get('file_path','—')}（{output.get('format','md')}，{output.get('size',0)}字节）")
                lines.append(f"内容预览：{output.get('preview','')[:200]}")
            elif name == "calendar":
                evts = output.get("events", [])
                status = output.get("status", "")
                if status == "added": lines.append(f"日程已添加：{evts[-1].get('title','') if evts else '—'} {evts[-1].get('date','') if evts else ''}")
                elif status == "exported": lines.append(f"已导出 {output.get('count',0)} 个日程为 iCal 格式")
                else: lines.append(f"当前 {len(evts)} 个日程：" + ", ".join(e.get('title','') for e in evts[:5]))
            elif name == "translate":
                lines.append(f"翻译结果（{output.get('source_lang','auto')}→{output.get('target_lang','zh')}）：{output.get('translated','')[:300]}")
            elif name == "clipboard":
                lines.append(f"剪贴板{output.get('action','')}：{output.get('content','')[:200]}")
            elif name in {"web_search", "search_and_fetch"}:
                results = output.get("results", [])
                summary=output.get("summary")
                lines.append(f"搜索「{output.get('query','')}」找到 {len(results)} 条结果：")
                if summary:lines.append(str(summary)[:1200])
                for r in results[:3]:
                    lines.append(f"  · {r.get('title','')[:80]} — {r.get('url','')[:60]}")
            elif name == "bash":
                if output.get("exit_code", 0) != 0:
                    lines.append(f"命令失败（退出码 {output.get('exit_code')}）：{str(output.get('stderr', ''))[:500]}")
                else:
                    lines.append(f"命令输出：{str(output.get('stdout', output.get('result', output)))[:500]}")
            elif name == "write_file":
                fp = output.get("file_path", "")
                sz = output.get("size", 0)
                if output.get("written"):
                    lines.append(f"文件已创建：{fp}（{sz} 字节）")
                else:
                    lines.append(f"文件创建失败：{output.get('error', fp)}")
            elif name == "read_file":
                lines.append(f"已读取：{output.get('file_path','')}（{output.get('total_lines',0)} 行）\n{output.get('content','')[:300]}")
            elif name == "edit_file":
                lines.append(f"已编辑：{output.get('file_path','')}，{output.get('replacements',0)} 处替换")
            elif name in ("glob", "grep"):
                lines.append(f"[{name}] 找到 {output.get('count',0)} 个匹配")
            elif name == "spawn_agent":
                summary = output.get("summary", "")
                results = output.get("results", [])
                lines.append(f"已派发 {len(results)} 个子 Agent：{summary}")
                for r in results[:3]:
                    lines.append(f"  · {r.get('agent','')}: {str(r.get('summary', r.get('changes', str(r)[:80])))[:120]}")
            elif name == "todo_write":
                todos = output.get("todos", [])
                if todos:
                    lines.append("任务列表：")
                    for t in todos[:5]:
                        status = {"pending":"⏳","in_progress":"🔄","completed":"✅"}.get(t.get("status",""),"❓")
                        lines.append(f"  {status} {t.get('content','')}")
                lines.append(output.get("summary", ""))
            elif name == "apply_changes":
                lines.append("已记录文件改动：" + ", ".join(output.get("files_modified", [])))
            else:
                lines.append(f"[{name}] 执行完成")
        if not lines:
            lines.append(f"已处理：{user_content[:200]}")
        return "\n".join(lines)

    # ── Format Translators ─────────────────────

    def _to_openai_tools(self, anthropic_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic tool format → OpenAI tool format。"""
        openai_tools = []
        for tool in anthropic_tools:
            schema = tool.get("input_schema", {})
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": schema.get("properties", {}),
                        "required": schema.get("required", []),
                    },
                },
            })
        return openai_tools

    def _to_openai_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """通用消息格式 → OpenAI 消息格式。"""
        result = []
        for msg in messages:
            role = msg.get("role", "user")
            if role not in ("user", "assistant", "system", "tool"):
                role = "user"
            content = msg.get("content", "")
            if isinstance(content, list):
                # Anthropic content blocks → flatten to string
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            text_parts.append(f"[tool_use: {block.get('name', '')}]")
                        elif block.get("type") == "tool_result":
                            text_parts.append(str(block.get("content", "")))
                    else:
                        text_parts.append(str(block))
                content = "\n".join(text_parts)
            result.append({"role": role, "content": str(content)})
        if not result:
            result.append({"role": "user", "content": "Please begin."})
        return result
