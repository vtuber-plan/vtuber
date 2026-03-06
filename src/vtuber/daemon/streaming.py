"""Streaming utilities — shared agent query/response patterns and logging."""

import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Literal

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
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


async def iter_response(
    agent: ClaudeSDKClient,
    query: str,
    *,
    log_source: str = "agent",
) -> AsyncIterator[AgentEvent]:
    """Query an agent and yield typed events.

    Yields:
        AgentEvent with type "text" (streaming text chunk),
        "tool" (tool call started), or "result" (query complete).
    """
    await agent.query(query)
    async for msg in agent.receive_response():
        log_stream_event(msg, log_source)

        tool_name = extract_tool_use_start(msg)
        if tool_name:
            yield AgentEvent(type="tool", tool=tool_name)
            continue

        text = extract_stream_text(msg)
        if text:
            yield AgentEvent(type="text", text=text)
        elif isinstance(msg, ResultMessage):
            yield AgentEvent(type="result")


async def collect_response(
    agent: ClaudeSDKClient,
    query: str,
    *,
    log_source: str = "agent",
) -> str:
    """Query an agent and return the full collected text response."""
    collected = ""
    async for event in iter_response(agent, query, log_source=log_source):
        if event.type == "text":
            collected += event.text
    return collected
