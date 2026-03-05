"""Heartbeat tool - allows the agent to signal aliveness and track session uptime."""

import json
from datetime import datetime
from typing import Any

from claude_agent_sdk import tool

_session_start: datetime | None = None
_beat_count = 0


@tool(
    "heartbeat",
    "Record a heartbeat pulse. Use this periodically to signal that the agent is alive and to track session uptime. Returns session duration and beat count.",
    {"type": "object", "properties": {}, "required": []},
)
async def heartbeat(args: dict[str, Any]) -> dict[str, Any]:
    global _session_start, _beat_count

    now = datetime.now()
    if _session_start is None:
        _session_start = now

    _beat_count += 1
    uptime = (now - _session_start).total_seconds()

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "beat": _beat_count,
                        "timestamp": now.isoformat(),
                        "uptime_seconds": uptime,
                        "session_start": _session_start.isoformat(),
                    }
                ),
            }
        ]
    }
