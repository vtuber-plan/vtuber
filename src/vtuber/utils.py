"""Shared utilities for VTuber."""

from claude_agent_sdk.types import (
    AssistantMessage,
    StreamEvent,
    TextBlock,
)


def extract_stream_text(msg) -> str | None:
    """Extract text from a StreamEvent or AssistantMessage."""
    if isinstance(msg, StreamEvent):
        event = msg.event
        if event.get("type") == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                return delta.get("text", "")
        return None

    if isinstance(msg, AssistantMessage):
        parts = []
        for block in msg.content:
            if isinstance(block, TextBlock) and block.text:
                parts.append(block.text)
        return "".join(parts) if parts else None

    return None
