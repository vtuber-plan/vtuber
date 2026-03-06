"""Schedule tools using APScheduler for precise task execution."""

import asyncio
from typing import Any

from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

# Injected by daemon on startup
_scheduler = None
_task_queue: asyncio.Queue | None = None


def set_scheduler(scheduler):
    """Set the scheduler instance (called by daemon on startup)."""
    global _scheduler
    _scheduler = scheduler


def set_task_queue(queue: asyncio.Queue):
    """Set the task queue for communicating scheduled tasks to the daemon."""
    global _task_queue
    _task_queue = queue


async def scheduled_job_handler(task: str = ""):
    """Async handler called by APScheduler when a scheduled job fires.

    This is a named, importable function (not a lambda) so that APScheduler
    can serialize it to the SQLAlchemy job store. It puts the task prompt
    into an asyncio.Queue that the daemon consumes.
    """
    if _task_queue and task:
        await _task_queue.put(task)


@tool(
    "schedule_create",
    "Create a scheduled task for the agent to execute at a specific time or interval.",
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
                "description": (
                    "Type of trigger: "
                    "'date' (one-time at specific datetime), "
                    "'interval' (recurring every N hours/minutes), "
                    "'cron' (cron-style recurring)"
                ),
            },
            "trigger_config": {
                "type": "object",
                "description": (
                    "Trigger parameters passed to APScheduler. Examples:\n"
                    "- date: {\"run_date\": \"2026-07-22 09:00:00\"}\n"
                    "- interval: {\"hours\": 1} or {\"minutes\": 30}\n"
                    "- cron: {\"hour\": 8, \"minute\": 0} for daily at 8am\n"
                    "- cron: {\"day_of_week\": \"mon-fri\", \"hour\": 9} for weekdays at 9am\n"
                    "Timezone: add \"timezone\": \"Asia/Shanghai\" to any trigger_config "
                    "for timezone-aware scheduling (IANA timezone names)."
                ),
            },
        },
        "required": ["task_id", "task", "trigger_type", "trigger_config"],
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
    trigger_type = args["trigger_type"]
    trigger_config = args.get("trigger_config", {})

    try:
        _scheduler.scheduler.add_job(
            func=scheduled_job_handler,
            trigger=trigger_type,
            id=task_id,
            kwargs={"task": task_prompt},
            replace_existing=True,
            **trigger_config,
        )

        # Read back the job to confirm next run time
        job = _scheduler.scheduler.get_job(task_id)
        next_run = (
            job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
            if job and job.next_run_time
            else "pending"
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Created scheduled task '{task_id}': {task_prompt} (next run: {next_run})",
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
        task_desc = job.kwargs.get("task", "N/A") if job.kwargs else "N/A"
        lines.append(f"- {job.id}: {task_desc} (next: {next_run})")

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
