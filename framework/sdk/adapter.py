"""ClaudeAdapter v1.0 — 双模适配器 + 速率限制 + 多模型路由。

支持:
  - Live 模式: 真实 Claude API
  - Echo 模式: 模拟执行（无 API key 时）
  - Rate limiting: 令牌桶算法
  - Multi-model: 不同任务用不同模型
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from typing import Any

_ANTHROPIC_AVAILABLE = False
try:
    from anthropic import AsyncAnthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    pass


class RateLimiter:
    """令牌桶速率限制器。"""

    def __init__(self, rate: float = 5.0, burst: int = 10):
        """
        Args:
            rate: 每秒补充令牌数
            burst: 桶容量（允许的突发请求数）
        """
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取一个令牌，必要时等待。"""
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


class ClaudeAdapter:
    """Claude SDK 适配层 v1.0。

    职责：封装 Claude API 调用 + tool use 循环 + 速率限制。
    不负责：prompt 构建、tool schema 定义——这些由 Agent/Skill 层负责。
    """

    # 多模型路由表
    DEFAULT_MODELS = {
        "orchestrator": "claude-opus-4-7",
        "research": "claude-sonnet-4-6",
        "code": "claude-sonnet-4-6",
        "default": "claude-sonnet-4-6",
    }

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        max_tool_rounds: int = 10,
        rate_limit: float = 5.0,
        rate_burst: int = 10,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_tool_rounds = max_tool_rounds
        self._live = _ANTHROPIC_AVAILABLE and bool(self.api_key)
        self._rate_limiter = RateLimiter(rate=rate_limit, burst=rate_burst)
        self._models = dict(self.DEFAULT_MODELS)

    @property
    def mode(self) -> str:
        return "live" if self._live else "echo"

    # ── Multi-model Routing ───────────────────

    def set_model_for_role(self, role: str, model: str) -> None:
        """配置角色 → 模型映射。"""
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

        if self._live:
            await self._rate_limiter.acquire()
            return await self._run_live(
                system_prompt, messages, tools, tool_handlers,
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

    # ── Live Mode ──────────────────────────────

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
        client = AsyncAnthropic(api_key=self.api_key)
        tool_calls: list[dict[str, Any]] = []
        total_input = 0
        total_output = 0

        anthropic_messages = self._to_anthropic_messages(messages)

        for _round in range(self.max_tool_rounds):
            kwargs: dict[str, Any] = {
                "model": model,
                "system": system_prompt,
                "messages": anthropic_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if tools:
                kwargs["tools"] = tools

            response = await client.messages.create(**kwargs)

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            tool_use_blocks = [
                b for b in response.content if b.type == "tool_use"
            ]

            if not tool_use_blocks:
                text = "\n".join(
                    b.text for b in response.content if b.type == "text"
                )
                return {
                    "content": text,
                    "tool_calls": tool_calls,
                    "usage": {"input_tokens": total_input, "output_tokens": total_output},
                    "model": response.model,
                    "mode": "live",
                }

            anthropic_messages.append({
                "role": "assistant",
                "content": [b.model_dump() for b in response.content],
            })

            tool_results = []
            for block in tool_use_blocks:
                tool_name = block.name
                tool_input = block.input
                handler = (tool_handlers or {}).get(tool_name)

                if handler:
                    if asyncio.iscoroutinefunction(handler):
                        output = await handler(**tool_input)
                    else:
                        output = handler(**tool_input)
                else:
                    output = {"error": f"No handler for tool '{tool_name}'"}

                tool_calls.append({
                    "name": tool_name,
                    "input": tool_input,
                    "output": output,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output, ensure_ascii=False),
                })

            anthropic_messages.append({
                "role": "user",
                "content": tool_results,
            })

        return {
            "content": "[max tool rounds exceeded]",
            "tool_calls": tool_calls,
            "usage": {"input_tokens": total_input, "output_tokens": total_output},
            "model": model,
            "mode": "live",
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

        if tools and tool_handlers:
            for tool in tools:
                tool_name = tool["name"]
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
            content = f"[Echo] Processed: {user_content[:200]}"

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": {"input_tokens": len(system_prompt) // 4, "output_tokens": len(content) // 4},
            "model": f"{self.model} (echo)",
            "mode": "echo",
        }

    def _echo_extract_input(
        self, tool: dict[str, Any], user_content: str, system_prompt: str
    ) -> dict[str, Any]:
        schema = tool.get("input_schema", {})
        properties = schema.get("properties", {})
        result: dict[str, Any] = {}
        for prop_name, prop_schema in properties.items():
            prop_type = prop_schema.get("type", "string")
            if prop_type == "string":
                if any(kw in prop_name.lower() for kw in ("query", "description", "task")):
                    result[prop_name] = user_content[:500] if isinstance(user_content, str) else str(user_content)
                elif "file" in prop_name.lower() or "path" in prop_name.lower():
                    result[prop_name] = "main.py"
                else:
                    result[prop_name] = user_content[:200] if isinstance(user_content, str) else str(user_content)
            elif prop_type == "array":
                result[prop_name] = []
            elif prop_type == "object":
                result[prop_name] = {}
            elif prop_type in ("integer", "number"):
                result[prop_name] = 0
            elif prop_type == "boolean":
                result[prop_name] = False
        return result

    def _echo_synthesize(
        self, system_prompt: str, user_content: str, tool_calls: list[dict[str, Any]]
    ) -> str:
        parts = [f"[Echo Mode] Analysis complete for: {user_content[:100]}"]
        for tc in tool_calls:
            output = tc.get("output", {})
            if isinstance(output, dict):
                summary_key = (
                    "summary" if "summary" in output
                    else "changes" if "changes" in output
                    else next(iter(output.keys()), "result")
                )
                parts.append(f"  [{tc['name']}] → {str(output.get(summary_key, output))[:200]}")
        return "\n".join(parts)

    def _to_anthropic_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result = []
        for msg in messages:
            role = msg.get("role", "user")
            if role not in ("user", "assistant"):
                role = "user"
            result.append({"role": role, "content": str(msg.get("content", ""))})
        if not result:
            result.append({"role": "user", "content": "Please begin."})
        return result
