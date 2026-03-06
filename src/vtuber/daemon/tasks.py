"""Scheduled task execution — consumes tasks from the queue and runs them via subagents."""

import asyncio
import logging

from vtuber.daemon.agents import create_agent
from vtuber.daemon.gateway import Gateway
from vtuber.daemon.protocol import MessageType
from vtuber.daemon.streaming import iter_response, truncate

logger = logging.getLogger("vtuber.daemon")


class ScheduledTaskRunner:
    """Consumes scheduled tasks from a queue and executes them via subagents."""

    def __init__(self, gateway: Gateway, task_queue: asyncio.Queue):
        self.gateway = gateway
        self.task_queue = task_queue
        self._consumer_task: asyncio.Task | None = None

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
                task_prompt = await self.task_queue.get()
                try:
                    await self._execute(task_prompt)
                except Exception as e:
                    logger.error("Scheduled task error: %s", e, exc_info=True)
                finally:
                    self.task_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _execute(self, task_prompt: str):
        """Execute a scheduled task using a temporary subagent."""
        logger.info("[schedule] executing: %s", truncate(task_prompt))

        try:
            agent = await create_agent(
                prompt_suffix="You are executing a scheduled task. Respond concisely.",
            )
            try:
                stream_id = f"task_{id(agent)}"
                index = 0

                async for event in iter_response(
                    agent,
                    f"[Scheduled Task] {task_prompt}",
                    log_source="schedule",
                ):
                    if event.type == "tool":
                        await self.gateway.broadcast({
                            "type": MessageType.PROGRESS,
                            "tool": event.tool,
                        })
                    elif event.type == "text":
                        await self.gateway.broadcast({
                            "type": MessageType.TASK_MESSAGE,
                            "stream_id": stream_id,
                            "index": index,
                            "content": event.text,
                            "task": task_prompt,
                            "done": False,
                        })
                        index += 1
                    elif event.type == "result":
                        await self.gateway.broadcast({
                            "type": MessageType.TASK_MESSAGE,
                            "stream_id": stream_id,
                            "index": index,
                            "content": "",
                            "task": task_prompt,
                            "done": True,
                        })
            finally:
                await agent.disconnect()

            logger.info("[schedule] completed: %s", truncate(task_prompt))

        except Exception as e:
            logger.error("[schedule] failed: %s — %s", truncate(task_prompt), e, exc_info=True)
            await self.gateway.broadcast({
                "type": MessageType.ERROR,
                "content": f"Error executing task '{task_prompt}': {str(e)}",
            })
