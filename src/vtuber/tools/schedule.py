"""Schedule tools using APScheduler for precise task execution."""

from typing import Any
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

# Note: Actual scheduler instance will be injected by daemon
_scheduler = None


def set_scheduler(scheduler):
    """Set the scheduler instance (called by daemon on startup)."""
    global _scheduler
    _scheduler = scheduler


@tool(
    "schedule_create",
    "Create a scheduled task for the agent to execute at a specific time or interval",
    {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Unique identifier for this task",
            },
            "task": {
                "type": "string",
                "description": "Description of the task for the agent to execute",
            },
            "trigger_type": {
                "type": "string",
                "enum": ["date", "interval", "cron"],
                "description": "Type of trigger: 'date' (one-time), 'interval' (recurring), 'cron' (cron expression)",
            },
            "trigger_config": {
                "type": "object",
                "description": "Trigger configuration (e.g., {'run_date': '2026-03-05 18:00:00'} or {'hours': 1})",
            },
        },
        "required": ["task_id", "task", "trigger_type"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def schedule_create(args: dict[str, Any]) -> dict[str, Any]:
    """Create a scheduled task."""
    if _scheduler is None:
        return {
            "content": [
                {"type": "text", "text": "Error: Scheduler not initialized. Daemon must be running."}
            ]
        }

    task_id = args["task_id"]
    task_prompt = args["task"]
    trigger_type = args.get("trigger_type", "date")
    trigger_config = args.get("trigger_config", {})

    # Add job to scheduler
    try:
        _scheduler.scheduler.add_job(
            func=lambda: None,  # Placeholder, daemon will intercept
            trigger=trigger_type,
            id=task_id,
            kwargs={"task": task_prompt},
            **trigger_config,
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Created scheduled task '{task_id}': {task_prompt}",
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error creating task: {str(e)}"}]
        }


@tool(
    "schedule_list",
    "List all scheduled tasks",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def schedule_list(args: dict[str, Any]) -> dict[str, Any]:
    """List all scheduled tasks."""
    if _scheduler is None:
        return {
            "content": [
                {"type": "text", "text": "Error: Scheduler not initialized. Daemon must be running."}
            ]
        }

    jobs = _scheduler.scheduler.get_jobs()
    if not jobs:
        return {"content": [{"type": "text", "text": "No scheduled tasks."}]}

    lines = ["Scheduled tasks:"]
    for job in jobs:
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "N/A"
        lines.append(f"- {job.id}: {job.kwargs.get('task', 'N/A')} (next: {next_run})")

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "schedule_cancel",
    "Cancel a scheduled task",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "ID of the task to cancel"},
        },
        "required": ["task_id"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
)
async def schedule_cancel(args: dict[str, Any]) -> dict[str, Any]:
    """Cancel a scheduled task."""
    if _scheduler is None:
        return {
            "content": [
                {"type": "text", "text": "Error: Scheduler not initialized. Daemon must be running."}
            ]
        }

    task_id = args["task_id"]
    try:
        _scheduler.scheduler.remove_job(task_id)
        return {"content": [{"type": "text", "text": f"Cancelled task '{task_id}'"}]}
    except Exception:
        return {
            "content": [{"type": "text", "text": f"Task '{task_id}' not found or already completed"}]
        }
