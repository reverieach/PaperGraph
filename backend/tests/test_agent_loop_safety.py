"""Tool-loop timeout, argument, output-cap, and trace contract tests."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from app.services.llm.agent_loop import ToolSpec, run_agent_loop


def _tool_call(name: str, arguments: str, call_id: str = "call-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class _ToolLoopLLM:
    def __init__(self, calls: list[list[SimpleNamespace]], final: str = "final answer") -> None:
        self._calls = list(calls)
        self.final = final
        self.messages: list[list[dict]] = []

    async def achat_with_tools(self, messages, schemas, **kwargs):
        self.messages.append([dict(item) for item in messages])
        tool_calls = self._calls.pop(0) if self._calls else []
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="" if tool_calls else self.final,
                        tool_calls=tool_calls,
                    )
                )
            ]
        )

    async def achat(self, messages, **kwargs):
        self.messages.append([dict(item) for item in messages])
        return SimpleNamespace(content=self.final)


def _run(*, llm: _ToolLoopLLM, tools: list[ToolSpec], **kwargs) -> str:
    return asyncio.run(
        run_agent_loop(
            llm=llm,
            system_prompt="system",
            history=[],
            user_prompt="question",
            tools=tools,
            **kwargs,
        )
    )


def _last_tool_message(llm: _ToolLoopLLM) -> str:
    flattened = [item for batch in llm.messages for item in batch]
    tools = [item for item in flattened if item.get("role") == "tool"]
    assert tools
    return str(tools[-1]["content"])


def test_agent_loop_returns_structured_invalid_argument_and_unknown_tool_errors() -> None:
    invalid_llm = _ToolLoopLLM([[_tool_call("safe", "not-json")]])
    events: list[dict] = []
    assert _run(
        llm=invalid_llm,
        tools=[ToolSpec("safe", "safe", {"type": "object"}, lambda args: "ok")],
        on_tool_event=events.append,
    ) == "final answer"
    assert '"code":"INVALID_TOOL_ARGUMENTS"' in _last_tool_message(invalid_llm)
    assert events[-1]["code"] == "INVALID_TOOL_ARGUMENTS"

    unknown_llm = _ToolLoopLLM([[_tool_call("unknown", "{}")]])
    assert _run(llm=unknown_llm, tools=[]) == "final answer"
    # No schemas means the model goes through achat directly, so use an
    # available schema plus a mismatched call to exercise the unknown path.
    unknown_llm = _ToolLoopLLM([[_tool_call("unknown", "{}")]])
    assert _run(
        llm=unknown_llm,
        tools=[ToolSpec("safe", "safe", {"type": "object"}, lambda args: "ok")],
    ) == "final answer"
    assert '"code":"UNKNOWN_TOOL"' in _last_tool_message(unknown_llm)


def test_agent_loop_enforces_wait_timeout_without_exposing_internal_error() -> None:
    def slow(_: dict) -> str:
        time.sleep(0.12)
        return "late result"

    llm = _ToolLoopLLM([[_tool_call("slow", "{}")]])
    events: list[dict] = []
    started = time.monotonic()
    assert _run(
        llm=llm,
        tools=[ToolSpec("slow", "slow", {"type": "object"}, slow, timeout_sec=0.01)],
        tool_deadline_sec=0.5,
        on_tool_event=events.append,
    ) == "final answer"
    # asyncio.run waits for its default executor to close, so this is not a
    # claim that the underlying worker thread was killed. The observable
    # contract is the bounded caller result and structured degradation code.
    assert '"code":"TOOL_TIMEOUT"' in _last_tool_message(llm)
    assert events[-1]["status"] == "timeout"
    assert time.monotonic() - started < 1.0


def test_agent_loop_caps_tool_output_and_limits_total_calls() -> None:
    llm = _ToolLoopLLM(
        [[_tool_call("large", "{}", "call-a"), _tool_call("large", "{}", "call-b")]]
    )
    events: list[dict] = []
    assert _run(
        llm=llm,
        tools=[
            ToolSpec(
                "large",
                "large",
                {"type": "object"},
                lambda args: "very long tool payload " * 1_000,
                max_output_tokens=40,
            )
        ],
        max_tool_calls=1,
        on_tool_event=events.append,
    ) == "final answer"
    flattened = [item for batch in llm.messages for item in batch]
    tool_messages = [str(item["content"]) for item in flattened if item.get("role") == "tool"]
    assert any("[TOOL_OUTPUT_TRUNCATED]" in message for message in tool_messages)
    assert any('"code":"TOOL_CALL_LIMIT_REACHED"' in message for message in tool_messages)
    assert any(event.get("output_truncated") is True for event in events)
    assert any(event.get("code") == "TOOL_CALL_LIMIT_REACHED" for event in events)
