"""Schedule tools using APScheduler for precise task execution."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

from vtuber.tools._helpers import error_response, text_response

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


async def scheduled_job_handler(task: str = "", deliver: bool = True):
    """Async handler called by APScheduler when a scheduled job fires.

    This is a named, importable function (not a lambda) so that APScheduler
    can serialize it to the SQLAlchemy job store. It puts the task payload
    into an asyncio.Queue that the daemon consumes.
    """
    if _task_queue and task:
        await _task_queue.put({"task": task, "deliver": deliver})


@tool(
    "schedule_create",
    "Create a scheduled task. Provide exactly ONE of: offset_seconds (one-time after delay), "
    "at (one-time at datetime), every_seconds (recurring interval), or cron (cron expression).",
    {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Unique identifier for this task",
            },
            "task": {
                "type": "string",
                "description": "Description of the task for the agent to execute when triggered",
            },
            "offset_seconds": {
                "type": "integer",
                "description": "Run once after this many seconds from now",
            },
            "at": {
                "type": "string",
                "description": "Run once at this ISO datetime (e.g. '2026-07-22T09:00:00')",
            },
            "every_seconds": {
                "type": "integer",
                "description": "Run repeatedly every N seconds",
            },
            "cron": {
                "type": "string",
                "description": "Cron expression for recurring schedule (e.g. '0 9 * * *' for daily 9am)",
            },
            "tz": {
                "type": "string",
                "description": "IANA timezone (e.g. 'Asia/Shanghai'). Applies to cron and at.",
            },
            "deliver": {
                "type": "boolean",
                "description": "Whether to deliver the agent's response to the user (default true). Set false for silent background tasks.",
            },
        },
        "required": ["task_id", "task"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def schedule_create(args: dict[str, Any]) -> dict[str, Any]:
    """Create a scheduled task."""
    if _scheduler is None:
        return error_response("Scheduler not initialized. Daemon must be running.")

    task_id = args["task_id"]
    task_prompt = args["task"]
    deliver = args.get("deliver", True)
    tz_name = args.get("tz")

    offset = args.get("offset_seconds")
    at = args.get("at")
    every = args.get("every_seconds")
    cron_expr = args.get("cron")

    # Exactly one scheduling mode must be provided
    modes = sum(x is not None for x in (offset, at, every, cron_expr))
    if modes != 1:
        return error_response("Provide exactly one of: offset_seconds, at, every_seconds, cron")

    job_kwargs = {"task": task_prompt, "deliver": deliver}

    try:
        if offset is not None:
            run_date = datetime.now(timezone.utc) + timedelta(seconds=int(offset))
            _scheduler.scheduler.add_job(
                func=scheduled_job_handler,
                trigger="date",
                id=task_id,
                kwargs=job_kwargs,
                replace_existing=True,
                run_date=run_date,
            )
        elif at is not None:
            run_date = datetime.fromisoformat(at)
            kwargs: dict[str, Any] = {"run_date": run_date}
            if tz_name:
                kwargs["timezone"] = tz_name
            _scheduler.scheduler.add_job(
                func=scheduled_job_handler,
                trigger="date",
                id=task_id,
                kwargs=job_kwargs,
                replace_existing=True,
                **kwargs,
            )
        elif every is not None:
            _scheduler.scheduler.add_job(
                func=scheduled_job_handler,
                trigger="interval",
                id=task_id,
                kwargs=job_kwargs,
                replace_existing=True,
                seconds=int(every),
            )
        elif cron_expr is not None:
            from apscheduler.triggers.cron import CronTrigger

            trigger_kwargs: dict[str, Any] = {}
            if tz_name:
                trigger_kwargs["timezone"] = tz_name
            trigger = CronTrigger.from_crontab(cron_expr, **trigger_kwargs)
            _scheduler.scheduler.add_job(
                func=scheduled_job_handler,
                trigger=trigger,
                id=task_id,
                kwargs=job_kwargs,
                replace_existing=True,
            )

        # Read back job to confirm
        job = _scheduler.scheduler.get_job(task_id)
        next_run = (
            job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
            if job and job.next_run_time
            else "pending"
        )
        return text_response(f"Scheduled '{task_id}': {task_prompt} (next run: {next_run})")

    except Exception as e:
        return error_response(f"Failed to create task: {e}")


@tool(
    "schedule_list",
    "List all scheduled tasks with their next run times.",
    {
        "type": "object",
        "properties": {},
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def schedule_list(args: dict[str, Any]) -> dict[str, Any]:
    """List all scheduled tasks."""
    if _scheduler is None:
        return error_response("Scheduler not initialized. Daemon must be running.")

    jobs = _scheduler.scheduler.get_jobs()
    if not jobs:
        return text_response("No scheduled tasks.")

    lines = ["Scheduled tasks:"]
    for job in jobs:
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z") if job.next_run_time else "N/A"
        task_desc = job.kwargs.get("task", "N/A") if job.kwargs else "N/A"
        lines.append(f"- {job.id}: {task_desc} (next: {next_run})")

    return text_response("\n".join(lines))


@tool(
    "schedule_cancel",
    "Cancel a scheduled task by its ID.",
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
        return error_response("Scheduler not initialized. Daemon must be running.")

    task_id = args["task_id"]
    try:
        _scheduler.scheduler.remove_job(task_id)
        return text_response(f"Cancelled task '{task_id}'")
    except Exception:
        return error_response(f"Task '{task_id}' not found or already completed")
