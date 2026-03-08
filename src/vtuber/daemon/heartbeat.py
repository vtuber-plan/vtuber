"""Heartbeat — periodic tasks and conversation consolidation."""

import asyncio
import json
import logging

from vtuber.config import (
    get_config,
    get_heartbeat_path,
    get_history_path,
    get_long_term_memory_path,
    get_sessions_dir,
)
from vtuber.daemon.agents import build_agent_options
from vtuber.daemon.gateway import Gateway
from vtuber.daemon.protocol import MessageType
from vtuber.daemon.agent_query import iter_oneshot, truncate
from vtuber.templates import DEFAULT_HEARTBEAT

logger = logging.getLogger("vtuber.daemon")


# Heartbeat tool definition (from nanobot)
_HEARTBEAT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat",
            "description": "Report heartbeat decision after reviewing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skip", "run"],
                        "description": "skip = nothing to do, run = has active tasks",
                    },
                    "tasks": {
                        "type": "string",
                        "description": "Natural-language summary of active tasks (required for run)",
                    },
                },
                "required": ["action"],
            },
        },
    }
]


# Save memory tool definition (from nanobot)
_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


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

    async def _decide(self, heartbeat_content: str) -> tuple[str, str]:
        """Phase 1: ask LLM to decide skip/run via virtual tool call.

        Returns (action, tasks) where action is 'skip' or 'run'.
        """
        from claude_agent_sdk import query as sdk_query
        from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

        options = build_agent_options(
            system_prompt="You are a heartbeat agent. Call the heartbeat tool to report your decision.",
            tools=_HEARTBEAT_TOOL,
            tool_choice={"type": "tool", "name": "heartbeat"},
            include_mcp_tools=False,
            include_preset_tools=False,
            include_schedule=False,
        )

        prompt = (
            "Review the following HEARTBEAT.md and decide whether there are active tasks.\n\n"
            f"{heartbeat_content}"
        )

        # Collect tool call from AssistantMessage
        tool_args = None
        try:
            async for msg in sdk_query(prompt=prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock) and block.name == "heartbeat":
                            tool_args = block.input
                            break
                    if tool_args:
                        break
        except Exception as e:
            logger.error("[heartbeat] decision phase error: %s", e, exc_info=True)
            return "skip", ""

        if not tool_args:
            logger.warning("[heartbeat] no tool call found, defaulting to skip")
            return "skip", ""

        action = tool_args.get("action", "skip")
        tasks = tool_args.get("tasks", "")
        return action, tasks

    async def _execute_heartbeat(self):
        """Two-phase heartbeat: decision via tool call, then optional execution."""
        heartbeat_path = get_heartbeat_path()
        heartbeat_content = ""

        if heartbeat_path.exists():
            heartbeat_content = heartbeat_path.read_text(encoding="utf-8").strip()

        if not heartbeat_content or heartbeat_content == DEFAULT_HEARTBEAT.strip():
            logger.info("[heartbeat] skipped (default/empty HEARTBEAT.md)")
            return

        logger.info("[heartbeat] checking for tasks...")

        try:
            # Phase 1: Decision
            action, tasks = await self._decide(heartbeat_content)

            if action != "run":
                logger.info("[heartbeat] OK (nothing to report)")
                return

            # Phase 2: Execution
            logger.info("[heartbeat] tasks found, executing...")
            options = build_agent_options(
                prompt_suffix=(
                    "[HEARTBEAT] 请执行以下任务并报告结果。"
                ),
                include_schedule=True,
                include_preset_tools=True,
            )

            collected = ""
            async for event in iter_oneshot(
                f"[Heartbeat Tasks]\n\n{tasks}",
                options,
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

            logger.info("[heartbeat] completed")

        except Exception as e:
            logger.error("[heartbeat] error: %s", e, exc_info=True)

    async def _consolidate(self):
        """Auto-consolidate session messages into MEMORY.md + HISTORY.md via tool call."""
        from vtuber.config import get_sessions_dir
        from vtuber.tools.memory import SessionManager

        self._consolidation_running = True
        try:
            sessions_dir = get_sessions_dir()
            manager = SessionManager(sessions_dir)
            session = manager.get_or_create(self.session_id)

            # Check if consolidation needed
            keep_count = 25  # Keep last 25 messages
            if len(session.messages) <= keep_count:
                return
            if len(session.messages) - session.last_consolidated <= 0:
                return

            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return

            logger.info(
                "[consolidation] starting: %d messages to consolidate",
                len(old_messages),
            )

            # Build transcript
            lines = []
            for m in old_messages:
                if not m.get("content"):
                    continue
                ts = m.get("timestamp", "?")[:16]
                role = m.get("role", "?")
                content = m.get("content", "")
                lines.append(f"[{ts}] {role.upper()}: {content}")

            if not lines:
                return

            memory_path = get_long_term_memory_path()
            current_memory = ""
            if memory_path.exists():
                current_memory = memory_path.read_text(encoding="utf-8").strip()

            prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{chr(10).join(lines)}"""

            # Call LLM with save_memory tool
            from claude_agent_sdk import query as sdk_query
            from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

            options = build_agent_options(
                system_prompt="You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation.",
                tools=_SAVE_MEMORY_TOOL,
                tool_choice={"type": "tool", "name": "save_memory"},
                include_mcp_tools=False,
                include_preset_tools=False,
                include_schedule=False,
            )

            tool_args = None
            try:
                async for msg in sdk_query(prompt=prompt, options=options):
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, ToolUseBlock) and block.name == "save_memory":
                                tool_args = block.input
                                break
                        if tool_args:
                            break
            except Exception as e:
                logger.error("[consolidation] error: %s", e, exc_info=True)
                return

            if not tool_args:
                logger.warning("[consolidation] LLM did not call save_memory tool")
                return

            # Process results
            from vtuber.config import get_history_path

            if entry := tool_args.get("history_entry"):
                if not isinstance(entry, str):
                    entry = json.dumps(entry, ensure_ascii=False)
                history_path = get_history_path()
                with open(history_path, "a", encoding="utf-8") as f:
                    f.write(entry.rstrip() + "\n\n")

            if update := tool_args.get("memory_update"):
                if not isinstance(update, str):
                    update = json.dumps(update, ensure_ascii=False)
                if update != current_memory:
                    memory_path.write_text(update, encoding="utf-8")

            # Update session metadata
            session.last_consolidated = len(session.messages) - keep_count
            manager.save(session)

            logger.info("[consolidation] completed: consolidated up to message %d", session.last_consolidated)

        except Exception as e:
            logger.error("[consolidation] error: %s", e, exc_info=True)
        finally:
            self._consolidation_running = False
