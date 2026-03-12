"""Shared helpers for MCP tool responses."""

from typing import Any


def text_response(text: str) -> dict[str, Any]:
    """Build a standard text content response for MCP tools."""
    return {"content": [{"type": "text", "text": text}]}


def error_response(text: str) -> dict[str, Any]:
    """Build an error response for MCP tools."""
    return {"content": [{"type": "text", "text": f"Error: {text}"}], "is_error": True}
