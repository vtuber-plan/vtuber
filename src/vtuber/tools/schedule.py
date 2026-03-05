"""Schedule tool - allows the agent to create and manage timed reminders."""

import json
from datetime import datetime, timedelta
from typing import Any

from claude_agent_sdk import tool

# In-memory schedule store (replace with persistent storage for production)
_schedules: dict[str, dict[str, Any]] = {}
_schedule_counter = 0


def _next_id() -> str:
    global _schedule_counter
    _schedule_counter += 1
    return f"sched_{_schedule_counter}"


@tool(
    "schedule_create",
    "Create a scheduled reminder. The agent will be reminded after the specified delay.",
    {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Reminder message content"},
            "delay_seconds": {
                "type": "integer",
                "description": "Delay in seconds before the reminder fires",
                "minimum": 1,
            },
        },
        "required": ["message", "delay_seconds"],
    },
)
async def schedule_create(args: dict[str, Any]) -> dict[str, Any]:
    sched_id = _next_id()
    fire_at = datetime.now() + timedelta(seconds=args["delay_seconds"])
    _schedules[sched_id] = {
        "id": sched_id,
        "message": args["message"],
        "delay_seconds": args["delay_seconds"],
        "fire_at": fire_at.isoformat(),
        "status": "pending",
    }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "id": sched_id,
                        "message": args["message"],
                        "fire_at": fire_at.isoformat(),
                        "status": "pending",
                    }
                ),
            }
        ]
    }


@tool(
    "schedule_list",
    "List all scheduled reminders and their status.",
    {"type": "object", "properties": {}, "required": []},
)
async def schedule_list(args: dict[str, Any]) -> dict[str, Any]:
    # Update status based on current time
    now = datetime.now()
    for sched in _schedules.values():
        if sched["status"] == "pending" and datetime.fromisoformat(sched["fire_at"]) <= now:
            sched["status"] = "fired"

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(list(_schedules.values()), ensure_ascii=False),
            }
        ]
    }


@tool(
    "schedule_cancel",
    "Cancel a scheduled reminder by its ID.",
    {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The schedule ID to cancel"},
        },
        "required": ["id"],
    },
)
async def schedule_cancel(args: dict[str, Any]) -> dict[str, Any]:
    sched_id = args["id"]
    if sched_id not in _schedules:
        return {
            "content": [{"type": "text", "text": f"Schedule '{sched_id}' not found."}]
        }
    _schedules[sched_id]["status"] = "cancelled"
    return {
        "content": [{"type": "text", "text": f"Schedule '{sched_id}' cancelled."}]
    }


schedule = [schedule_create, schedule_list, schedule_cancel]
