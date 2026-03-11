"""Scheduled task execution — consumes tasks from the queue and runs them via the agent pool."""

import asyncio
import logging
from typing import TYPE_CHECKING

from vtuber.daemon.gateway import Gateway
from vtuber.daemon.protocol import MessageType
from vtuber.daemon.agent_query import AgentTimeoutError, iter_response, truncate

if TYPE_CHECKING:
    from vtuber.daemon.server import DaemonServer

logger = logging.getLogger("vtuber.daemon")


class ScheduledTaskRunner:
    """Consumes scheduled tasks from a queue and executes them via the main agent."""

    def __init__(self, server: "DaemonServer", task_queue: asyncio.Queue):
        self._server = server
        self.task_queue = task_queue
        self._consumer_task: asyncio.Task | None = None

    @property
    def gateway(self) -> Gateway:
        return self._server.gateway

    def start(self):
        """Start the task queue consumer as a background task."""
        self._consumer_task = asyncio.create_task(self._consume())

    async def stop(self):
        """Stop the task queue consumer."""
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

    async def _consume(self):
        """Consume scheduled tasks from the queue."""
        try:
            while True:
                payload = await self.task_queue.get()
                try:
                    # Support both dict payload and legacy string
                    if isinstance(payload, dict):
                        task_prompt = payload["task"]
                        deliver = payload.get("deliver", True)
                    else:
                        task_prompt = payload
                        deliver = True
                    await self._execute(task_prompt, deliver=deliver)
                except Exception as e:
                    logger.error("Scheduled task error: %s", e, exc_info=True)
                finally:
                    self.task_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _execute(self, task_prompt: str, *, deliver: bool = True):
        """Execute a scheduled task using a dedicated 'schedule' agent from the pool."""
        logger.info("[schedule] executing (deliver=%s): %s", deliver, truncate(task_prompt))

        session_id = "schedule"
        try:
            agent = await self._server.agent_pool.get(session_id)
        except Exception as e:
            logger.error("[schedule] failed to get agent: %s", e, exc_info=True)
            return

        try:
            step = 0
            lock = self._server._get_session_lock(session_id)
            async with lock:
                async for event in iter_response(
                    agent,
                    f"[Scheduled Task] {task_prompt}",
                    session_id=session_id,
                    log_source="schedule",
                ):
                    if not deliver:
                        continue
                    if event.type == "tool":
                        await self.gateway.broadcast({
                            "type": MessageType.PROGRESS,
                            "tool": event.tool,
                        })
                    elif event.type == "text":
                        await self.gateway.broadcast({
                            "type": MessageType.TASK_MESSAGE,
                            "step": step,
                            "content": event.text,
                            "task": task_prompt,
                            "done": False,
                        })
                        step += 1
                    elif event.type == "result":
                        await self.gateway.broadcast({
                            "type": MessageType.TASK_MESSAGE,
                            "step": step,
                            "content": "",
                            "task": task_prompt,
                            "done": True,
                        })

            logger.info("[schedule] completed: %s", truncate(task_prompt))

        except AgentTimeoutError as e:
            logger.error("[schedule] timeout: %s — %s", truncate(task_prompt), e)
            await self._server.agent_pool.kill_and_recreate(session_id)
        except Exception as e:
            logger.error("[schedule] failed: %s — %s", truncate(task_prompt), e, exc_info=True)
            if deliver:
                await self.gateway.broadcast({
                    "type": MessageType.ERROR,
                    "content": f"Error executing task '{task_prompt}': {str(e)}",
                })
