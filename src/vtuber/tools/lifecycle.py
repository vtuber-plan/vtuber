"""Lifecycle tools — allow the agent to restart itself."""

import asyncio
from typing import Any

from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

from vtuber.tools._helpers import text_response

# Signalled by the tool, consumed by the daemon after query completes.
_restart_event = asyncio.Event()


def consume_restart() -> bool:
    """Check and clear the restart flag. Returns True if restart was requested."""
    if _restart_event.is_set():
        _restart_event.clear()
        return True
    return False


@tool(
    "agent_restart",
    "Restart yourself (clear conversation context and reload config/plugins). "
    "Use when the user asks you to restart, reset, or reload, "
    "or after installing/removing plugins that require a reload.",
    {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Brief reason for the restart (shown in logs)",
            },
        },
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
)
async def agent_restart(args: dict[str, Any]) -> dict[str, Any]:
    """Signal that this agent session should be restarted after the current query."""
    reason = args.get("reason", "user requested")
    _restart_event.set()
    return text_response(f"Restart scheduled (reason: {reason}). This session will restart momentarily.")
