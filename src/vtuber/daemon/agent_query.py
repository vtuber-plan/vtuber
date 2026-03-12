"""Streaming utilities — shared agent query/response patterns and logging."""

import asyncio
import json
import logging
import os
import signal as signal_mod
from dataclasses import dataclass
from typing import AsyncIterator, Literal

from claude_agent_sdk import ClaudeSDKClient, query as sdk_query
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
)

from vtuber.utils import extract_stream_text, extract_tool_use_start

logger = logging.getLogger("vtuber.daemon")


def truncate(s: str, max_len: int = 200) -> str:
    """Truncate a string for log display."""
    s = s.replace("\n", " ").strip()
    return s[:max_len] + "..." if len(s) > max_len else s


def log_stream_event(msg, source: str = "agent"):
    """Log interesting stream events (tool calls, results, errors)."""
    if isinstance(msg, StreamEvent):
        event = msg.event
        if event.get("type") == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                logger.debug("[%s] tool_call: %s", source, block.get("name", "?"))

    elif isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                input_preview = truncate(json.dumps(block.input, ensure_ascii=False))
                logger.debug("[%s] tool_call: %s(%s)", source, block.name, input_preview)
            elif isinstance(block, TextBlock) and block.text:
                logger.debug("[%s] text: %s", source, truncate(block.text))

    elif isinstance(msg, ResultMessage):
        cost = f"${msg.total_cost_usd:.4f}" if msg.total_cost_usd else "n/a"
        logger.info(
            "[%s] result: turns=%d, cost=%s, duration=%dms",
            source, msg.num_turns, cost, msg.duration_ms,
        )


@dataclass
class AgentEvent:
    """Typed event from an agent response stream."""

    type: Literal["text", "tool", "result"]
    text: str = ""
    tool: str = ""


class AgentTimeoutError(Exception):
    """Raised when an agent query or response times out."""


_SENTINEL = object()


async def _safe_anext(aiter):
    """Await next item from an async iterator, returning _SENTINEL on exhaustion.

    Wraps StopAsyncIteration so the result is safe to use inside asyncio.Task
    (Tasks convert StopAsyncIteration to RuntimeError).
    """
    try:
        return await aiter.__anext__()
    except StopAsyncIteration:
        return _SENTINEL


def kill_agent_process(agent: ClaudeSDKClient) -> None:
    """Best-effort SIGKILL of the SDK's subprocess."""
    try:
        proc = agent._transport._process  # type: ignore[attr-defined]
        if proc and getattr(proc, "pid", None) and proc.returncode is None:
            os.kill(proc.pid, signal_mod.SIGKILL)
            logger.info("Killed agent subprocess (pid=%d)", proc.pid)
    except Exception as e:
        logger.debug("Could not kill agent subprocess: %s", e)


# ── Persistent agent streaming ────────────────────────────────────


async def iter_response(
    agent: ClaudeSDKClient,
    query: str,
    *,
    session_id: str = "default",
    log_source: str = "agent",
    query_timeout: float | None = None,
    idle_timeout: float | None = None,
) -> AsyncIterator[AgentEvent]:
    """Query a persistent agent and yield typed events.

    For one-shot (ephemeral) queries, use iter_oneshot() instead.

    Yields:
        AgentEvent with type "text" (streaming text chunk),
        "tool" (tool call started), or "result" (query complete).

    Raises:
        AgentTimeoutError: If agent.query() or receive_response() times out.
    """
    from vtuber.config import get_config

    if query_timeout is None:
        query_timeout = get_config().query_timeout
    if idle_timeout is None:
        idle_timeout = get_config().idle_timeout
    try:
        await asyncio.wait_for(agent.query(query, session_id=session_id), timeout=query_timeout)
    except asyncio.TimeoutError:
        raise AgentTimeoutError(
            f"agent.query() timed out after {query_timeout:.0f}s"
        ) from None

    logger.debug("[%s] query accepted, awaiting response stream", log_source)

    # Use asyncio.wait (NOT wait_for) so we don't cancel the __anext__ task
    # while the SDK's async generator is running.  On timeout we cancel
    # the task explicitly and close the generator before raising.
    aiter = agent.receive_response().__aiter__()
    pending_task: asyncio.Task | None = None
    try:
        while True:
            pending_task = asyncio.create_task(_safe_anext(aiter))
            done, _ = await asyncio.wait({pending_task}, timeout=idle_timeout)
            if not done:
                # Timeout — cancel the pending __anext__ task before raising
                pending_task.cancel()
                try:
                    await pending_task
                except (asyncio.CancelledError, Exception):
                    pass
                pending_task = None
                raise AgentTimeoutError(
                    f"receive_response() idle for {idle_timeout:.0f}s"
                )

            pending_task = None
            result = done.pop().result()
            if result is _SENTINEL:
                break

            msg = result
            log_stream_event(msg, log_source)

            # Skip sub-agent messages — only surface top-level agent output.
            if getattr(msg, "parent_tool_use_id", None):
                continue

            tool_name = extract_tool_use_start(msg)
            if tool_name:
                yield AgentEvent(type="tool", tool=tool_name)
                continue

            # Only process AssistantMessage, skip StreamEvent deltas
            if isinstance(msg, StreamEvent):
                continue

            text = extract_stream_text(msg)
            if text:
                yield AgentEvent(type="text", text=text)
            elif isinstance(msg, ResultMessage):
                yield AgentEvent(type="result")
    finally:
        # Cancel any in-flight task before closing the generator
        if pending_task and not pending_task.done():
            pending_task.cancel()
            try:
                await pending_task
            except (asyncio.CancelledError, Exception):
                pass
        # Safely close the async generator
        try:
            await aiter.aclose()
        except Exception:
            pass


async def collect_response(
    agent: ClaudeSDKClient,
    query: str,
    *,
    session_id: str = "default",
    log_source: str = "agent",
) -> str:
    """Query a persistent agent and return the full collected text response."""
    collected = ""
    async for event in iter_response(agent, query, session_id=session_id, log_source=log_source):
        if event.type == "text":
            collected += event.text
    return collected


# ── One-shot (ephemeral) query streaming ──────────────────────────


async def iter_oneshot(
    prompt: str,
    options: ClaudeAgentOptions,
    *,
    log_source: str = "oneshot",
) -> AsyncIterator[AgentEvent]:
    """Run a one-shot query and yield typed events.

    Uses sdk query() — automatically manages subprocess lifecycle.
    No manual connect/disconnect needed.
    """
    async for msg in sdk_query(prompt=prompt, options=options):
        log_stream_event(msg, log_source)

        # Skip sub-agent messages — only surface top-level agent output.
        if getattr(msg, "parent_tool_use_id", None):
            continue

        tool_name = extract_tool_use_start(msg)
        if tool_name:
            yield AgentEvent(type="tool", tool=tool_name)
            continue

        # Only process AssistantMessage, skip StreamEvent deltas
        if isinstance(msg, StreamEvent):
            continue

        text = extract_stream_text(msg)
        if text:
            yield AgentEvent(type="text", text=text)
        elif isinstance(msg, ResultMessage):
            yield AgentEvent(type="result")


async def collect_oneshot(
    prompt: str,
    options: ClaudeAgentOptions,
    *,
    log_source: str = "oneshot",
) -> str:
    """Run a one-shot query and return the full collected text response."""
    collected = ""
    async for event in iter_oneshot(prompt, options, log_source=log_source):
        if event.type == "text":
            collected += event.text
    return collected
