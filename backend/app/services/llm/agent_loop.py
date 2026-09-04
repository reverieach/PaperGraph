"""无状态 agent 循环 + 极简 Tool 协议。

替代 hello_agents.SimpleAgent 的工具调用循环。关键差异：
- **无状态**：history 由 caller 传入，不在实例上累积 → 天然并发安全，
  无需像 SimpleAgent 那样每请求新建实例。
- **caller 拥有 history**：调用方决定上下文如何拼接，循环只负责一轮工具编排。
- **ToolSpec 极简**：``fn`` + JSON schema，不继承框架 Tool 基类、无 circuit_breaker /
  expandable 等未用复杂度。

本阶段（阶段 1）独立存在、暂不被调用；阶段 5 起 paper_reader_reply 改用它。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Dict, Optional

from ..context import TokenCounter

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """函数式工具：``fn`` 接参数字典返回字符串，``parameters_schema`` 是 OpenAI JSON schema。

    与 hello_agents ``Tool`` 的关系：``Tool.to_openai_schema()`` 生成同形 schema，
    迁移期可由 ``Tool`` 子类的 ``build_tool_spec()`` 包装出 ToolSpec（见阶段 4）。
    """
    name: str
    description: str
    parameters_schema: Dict[str, Any]
    fn: Callable[[Dict[str, Any]], str | Awaitable[str]]
    # Timeouts bound how long the orchestrator waits.  A sync implementation
    # runs in a worker thread; timing out does not and cannot kill that thread,
    # so heavy PDF parsing must remain in the ingest worker rather than here.
    timeout_sec: float = 8.0
    max_output_tokens: int = 800
    allowed_source_types: tuple[str, ...] = ()


def _tool_error_payload(code: str, message: str) -> str:
    """Return a stable, non-sensitive tool failure contract for the model."""

    return json.dumps(
        {"status": "error", "code": str(code), "message": str(message)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _clip_tool_output(value: Any, *, max_tokens: int) -> tuple[str, bool]:
    """Apply a token cap after every tool call without breaking control flow."""

    text = value if isinstance(value, str) else str(value or "")
    counter = TokenCounter()
    limit = max(32, min(int(max_tokens or 0), 2_000))
    if counter.count(text) <= limit:
        return text, False
    return counter.clip(text, limit, suffix="\n[TOOL_OUTPUT_TRUNCATED]"), True


def _emit_tool_event(
    callback: Callable[[dict[str, Any]], None] | None,
    event: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(dict(event))
    except Exception:
        # Tracing must never make the Reader request fail.
        logger.debug("agent_loop_tool_trace_callback_failed", exc_info=True)


async def _invoke_tool(
    spec: ToolSpec,
    args: dict[str, Any],
    *,
    timeout_sec: float,
) -> Any:
    """Invoke sync/async tool code under an await-time deadline."""

    async def invoke() -> Any:
        if inspect.iscoroutinefunction(spec.fn):
            return await spec.fn(args)
        result = await asyncio.to_thread(spec.fn, args)
        # A callable wrapped by functools.partial may not report as a
        # coroutine function, but can still return an awaitable.
        if inspect.isawaitable(result):
            return await result
        return result

    return await asyncio.wait_for(invoke(), timeout=max(0.05, float(timeout_sec)))


async def run_agent_loop(
    *,
    llm: Any,
    system_prompt: str,
    history: List[Dict[str, str]],
    user_prompt: str,
    tools: Optional[List[ToolSpec]] = None,
    max_tool_iterations: int = 5,
    max_tool_calls: int = 8,
    tool_deadline_sec: float = 30.0,
    temperature: float = 0.3,
    on_tool_event: Callable[[dict[str, Any]], None] | None = None,
    **llm_kwargs: Any,
) -> str:
    """无状态 function-calling 循环。

    Args:
        llm: LLMClient（需提供 ``achat`` / ``achat_with_tools``）。
        system_prompt: 系统提示词。
        history: 对话历史（caller 拼好，每条 {"role","content"}）。循环不修改它。
        user_prompt: 本轮用户输入。
        tools: 可选工具列表；为空则单轮直答。
        max_tool_iterations: 最大工具调用轮数。
        max_tool_calls: 单次请求最多实际执行的工具数（跨轮累计）。
        tool_deadline_sec: 所有工具调用共享的等待期限；不会终止已启动的线程。
        temperature: 采样温度。
        on_tool_event: 可选的无敏感参数 trace callback。
        **llm_kwargs: 透传给 llm 的额外参数（如 max_tokens）。
    """
    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})

    schemas = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters_schema,
            },
        }
        for t in (tools or [])
    ]
    tool_map: Dict[str, ToolSpec] = {t.name: t for t in (tools or [])}

    if not schemas:
        result = await llm.achat(messages, temperature=temperature, **llm_kwargs)
        return result.content or ""

    last_content = ""
    tool_calls_used = 0
    max_calls = max(1, min(int(max_tool_calls), 32))
    loop = asyncio.get_running_loop()
    deadline_at = loop.time() + max(1.0, float(tool_deadline_sec))
    for _ in range(max_tool_iterations):
        resp = await llm.achat_with_tools(
            messages, schemas, tool_choice="auto", temperature=temperature, **llm_kwargs
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            last_content = getattr(msg, "content", None) or ""
            return last_content

        # 助手消息（含 tool_calls）原样追加，供下一轮模型看到调用上下文。
        messages.append({
            "role": "assistant",
            "content": getattr(msg, "content", None) or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            tool_name = tc.function.name
            tool_call_id = tc.id
            started = time.monotonic()
            event: dict[str, Any] = {"tool_name": str(tool_name or "")}
            if tool_calls_used >= max_calls:
                result = _tool_error_payload(
                    "TOOL_CALL_LIMIT_REACHED",
                    "本轮工具调用次数已达上限；请基于已有材料继续回答。",
                )
                event.update({"status": "rejected", "code": "TOOL_CALL_LIMIT_REACHED"})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result,
                })
                event["elapsed_ms"] = int((time.monotonic() - started) * 1_000)
                _emit_tool_event(on_tool_event, event)
                continue
            tool_calls_used += 1
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (TypeError, ValueError):
                result = _tool_error_payload(
                    "INVALID_TOOL_ARGUMENTS",
                    "工具参数不是合法 JSON 对象；请使用工具 schema 重新调用。",
                )
                event.update({"status": "rejected", "code": "INVALID_TOOL_ARGUMENTS"})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result,
                })
                event["elapsed_ms"] = int((time.monotonic() - started) * 1_000)
                _emit_tool_event(on_tool_event, event)
                continue
            if not isinstance(args, dict):
                result = _tool_error_payload(
                    "INVALID_TOOL_ARGUMENTS",
                    "工具参数必须是 JSON 对象；请使用工具 schema 重新调用。",
                )
                event.update({"status": "rejected", "code": "INVALID_TOOL_ARGUMENTS"})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result,
                })
                event["elapsed_ms"] = int((time.monotonic() - started) * 1_000)
                _emit_tool_event(on_tool_event, event)
                continue
            spec = tool_map.get(tool_name)
            if spec is None:
                result = _tool_error_payload(
                    "UNKNOWN_TOOL",
                    "请求的工具当前不可用；请基于已有材料继续回答。",
                )
                event.update({"status": "rejected", "code": "UNKNOWN_TOOL"})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result,
                })
                event["elapsed_ms"] = int((time.monotonic() - started) * 1_000)
                _emit_tool_event(on_tool_event, event)
                continue
            remaining = deadline_at - loop.time()
            if remaining <= 0:
                result = _tool_error_payload(
                    "TOOL_REQUEST_DEADLINE_EXCEEDED",
                    "本轮工具等待期限已到；请基于已有材料继续回答。",
                )
                event.update({"status": "timeout", "code": "TOOL_REQUEST_DEADLINE_EXCEEDED"})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result,
                })
                event["elapsed_ms"] = int((time.monotonic() - started) * 1_000)
                _emit_tool_event(on_tool_event, event)
                continue
            try:
                raw_result = await _invoke_tool(
                    spec,
                    args,
                    timeout_sec=min(max(0.05, float(spec.timeout_sec)), remaining),
                )
                result, truncated = _clip_tool_output(
                    raw_result,
                    max_tokens=spec.max_output_tokens,
                )
                event.update({"status": "ok", "output_truncated": truncated})
            except TimeoutError:
                logger.warning(
                    "agent_loop_tool_wait_timeout",
                    extra={"tool_name": tool_name, "timeout_sec": min(float(spec.timeout_sec), remaining)},
                )
                result = _tool_error_payload(
                    "TOOL_TIMEOUT",
                    "工具在限定时间内未返回，系统已停止等待；请基于已有材料继续回答。",
                )
                event.update({"status": "timeout", "code": "TOOL_TIMEOUT"})
            except Exception as exc:
                logger.warning(
                    "agent_loop_tool_failed",
                    extra={"tool_name": tool_name, "error_type": type(exc).__name__},
                    exc_info=True,
                )
                result = _tool_error_payload(
                    "TOOL_EXECUTION_FAILED",
                    "工具执行失败；请基于已有材料继续回答。",
                )
                event.update({"status": "error", "code": "TOOL_EXECUTION_FAILED"})
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result,
            })
            event["elapsed_ms"] = int((time.monotonic() - started) * 1_000)
            _emit_tool_event(on_tool_event, event)

    # 超过最大迭代轮数，做一次无工具收尾。
    final = await llm.achat(messages, temperature=temperature, **llm_kwargs)
    return final.content or last_content


def run_agent_loop_sync(**kwargs: Any) -> str:
    """同步入口：在无运行 event loop 时用 ``asyncio.run`` 驱动 ``run_agent_loop``。

    ``paper_reader_reply`` 跑在 FastAPI 的 ``run_in_threadpool`` 独立线程里（无 event loop），
    ``asyncio.run`` 安全。若意外在已有 loop 的上下文调用，退到临时线程池避免冲突。
    """
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False

    if not running:
        return asyncio.run(run_agent_loop(**kwargs))

    def _thread_entry() -> str:
        return asyncio.run(run_agent_loop(**kwargs))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_thread_entry).result()
