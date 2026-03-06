"""Heartbeat — periodic tasks and conversation consolidation."""

import asyncio
import json
import logging

from vtuber.config import (
    get_config,
    get_consolidation_state_path,
    get_heartbeat_path,
    get_history_path,
    get_long_term_memory_path,
    get_sessions_dir,
)
from vtuber.daemon.agents import create_agent
from vtuber.daemon.gateway import Gateway
from vtuber.daemon.protocol import MessageType
from vtuber.daemon.streaming import collect_response, iter_response, truncate
from vtuber.templates import DEFAULT_HEARTBEAT

logger = logging.getLogger("vtuber.daemon")


class HeartbeatManager:
    """Manages periodic heartbeat checks and conversation consolidation."""

    def __init__(self, gateway: Gateway, session_id: str, interval_minutes: int):
        self.gateway = gateway
        self.session_id = session_id
        self.interval = interval_minutes
        self.message_count = 0
        self._heartbeat_task: asyncio.Task | None = None
        self._consolidation_task: asyncio.Task | None = None
        self._consolidation_running = False

    def start(self):
        """Start the heartbeat loop as a background task."""
        self._heartbeat_task = asyncio.create_task(self._loop())
        logger.info("Heartbeat started (every %d min)", self.interval)

    async def stop(self):
        """Stop heartbeat and consolidation tasks."""
        for task in (self._heartbeat_task, self._consolidation_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def on_message(self):
        """Called after each user message to track consolidation threshold."""
        self.message_count += 1
        if self.message_count >= 50 and not self._consolidation_running:
            self._consolidation_task = asyncio.create_task(self._consolidate())

    async def _loop(self):
        """Periodic heartbeat loop."""
        while True:
            try:
                await asyncio.sleep(self.interval * 60)
                await self._execute_heartbeat()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Heartbeat error: %s", e, exc_info=True)

    async def _execute_heartbeat(self):
        """Two-phase heartbeat: pre-check file, then optionally call sub-agent."""
        heartbeat_path = get_heartbeat_path()
        heartbeat_content = ""

        if heartbeat_path.exists():
            heartbeat_content = heartbeat_path.read_text(encoding="utf-8").strip()

        if not heartbeat_content or heartbeat_content == DEFAULT_HEARTBEAT.strip():
            logger.info("[heartbeat] skipped (default/empty HEARTBEAT.md)")
            return

        logger.info("[heartbeat] tasks found, executing with sub-agent")
        try:
            agent = await create_agent(
                prompt_suffix=(
                    "[HEARTBEAT] 请审查以下任务清单，决定是否有需要执行的任务。\n"
                    "如果有任务需要执行，请执行它们并报告结果。\n"
                    "如果没有需要执行的任务，请只回复空内容。"
                ),
                include_schedule=True,
            )
            try:
                collected = ""
                async for event in iter_response(
                    agent,
                    f"[Heartbeat Task Checklist]\n\n{heartbeat_content}",
                    log_source="heartbeat",
                ):
                    if event.type == "text":
                        collected += event.text

                if collected.strip():
                    logger.info("[heartbeat] agent responded: %s", truncate(collected))
                    await self.gateway.broadcast({
                        "type": MessageType.HEARTBEAT_MESSAGE,
                        "content": collected,
                    })
                else:
                    logger.info("[heartbeat] agent found nothing to do")
            finally:
                await agent.disconnect()

            logger.info("[heartbeat] completed")

        except Exception as e:
            logger.error("[heartbeat] error: %s", e, exc_info=True)

    async def _consolidate(self):
        """Auto-consolidate session messages into long-term memory + history log."""
        self._consolidation_running = True
        try:
            session_path = get_sessions_dir() / f"{self.session_id}.jsonl"
            if not session_path.exists():
                return

            # Read consolidation state
            state_path = get_consolidation_state_path()
            last_consolidated = 0
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if state.get("session_id") == self.session_id:
                        last_consolidated = state.get("last_consolidated", 0)
                except Exception:
                    pass

            lines = session_path.read_text(encoding="utf-8").strip().split("\n")
            new_lines = lines[last_consolidated:]
            if len(new_lines) < 20:
                return

            # Build readable transcript
            transcript_parts = []
            for raw_line in new_lines:
                try:
                    entry = json.loads(raw_line)
                    ts = entry.get("timestamp", "?")[:16]
                    role = entry.get("role", "?")
                    content = entry.get("content", "")[:500]
                    transcript_parts.append(f"[{ts}] {role}: {content}")
                except json.JSONDecodeError:
                    continue

            if not transcript_parts:
                return

            transcript = "\n".join(transcript_parts)
            memory_path = get_long_term_memory_path()
            history_path = get_history_path()
            current_memory = ""
            if memory_path.exists():
                current_memory = memory_path.read_text(encoding="utf-8").strip()

            logger.info(
                "[consolidation] starting: %d new messages (from %d)",
                len(new_lines), last_consolidated,
            )

            agent = await create_agent(
                system_prompt=(
                    "你是一个记忆整理助手。你的任务是：\n"
                    f"1. 阅读下面的对话记录\n"
                    f"2. 将有价值的长期事实更新到 {memory_path}\n"
                    f"3. 在 {history_path} 末尾追加一段摘要（以 [YYYY-MM-DD HH:MM] 开头）\n\n"
                    "长期记忆应该按主题组织，保持简洁（不超过200行）。\n"
                    "历史摘要应该是2-5句话，概括对话中的关键事件和决策。\n"
                    "不要删除长期记忆中已有的仍然有效的内容。"
                ),
                include_mcp_tools=False,
                include_preset_tools=True,
            )
            try:
                await collect_response(
                    agent,
                    f"## 当前长期记忆\n\n{current_memory or '(空)'}\n\n"
                    f"## 需要整理的对话记录\n\n{transcript}",
                    log_source="consolidation",
                )
            finally:
                await agent.disconnect()

            # Update consolidation state
            total_consolidated = last_consolidated + len(new_lines)
            state_path.write_text(
                json.dumps({
                    "session_id": self.session_id,
                    "last_consolidated": total_consolidated,
                }),
                encoding="utf-8",
            )
            self.message_count = 0
            logger.info("[consolidation] completed: consolidated up to message %d", total_consolidated)

        except Exception as e:
            logger.error("[consolidation] error: %s", e, exc_info=True)
        finally:
            self._consolidation_running = False
