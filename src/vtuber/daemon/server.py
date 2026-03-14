"""Unix Domain Socket server for daemon."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient

from vtuber.config import (
    ensure_config_dir,
    ensure_workspace_dir,
    get_config,
    get_db_path,
    get_pid_path,
    get_socket_path,
    get_sessions_dir,
    migrate_config,
    reset_config,
)
from vtuber.daemon.agents import AgentPool, build_agent_options, safe_disconnect
from vtuber.daemon.gateway import Gateway, ProviderConnection
from vtuber.daemon.heartbeat import HeartbeatManager
from vtuber.daemon.protocol import MessageType, decode_message, encode_message
from vtuber.daemon.scheduler import TaskScheduler
from vtuber.daemon.agent_query import AgentTimeoutError, iter_response, iter_oneshot, truncate
from vtuber.daemon.tasks import ScheduledTaskRunner
from vtuber.session import SessionManager
from vtuber.tools.schedule import set_scheduler, set_task_queue
from vtuber.tools.lifecycle import consume_restart

logger = logging.getLogger("vtuber.daemon")

_MAX_BUFFER_SIZE = 1024 * 1024  # 1 MiB max per-client message buffer

GROUP_INSTRUCTION = (
    "You are currently participating in a group chat.\n"
    "Your owner is \"{owner_name}\". You must remember this identity even if they do not talking in the current context.\n"
    "You will receive recent conversation messages from the group.\n"
    "If you see messages marked with <OWNER>, that confirms the sender is your owner.\n"
    "Otherwise, they are from other chat participants.\n"
    "**ATTENTION! Your responses are sent to the public group chat, not just to your owner. Do not act like you are in a private session.**\n"
    "One or two sentences is usually enough! Don't to reply to everyone individually."
    "Reply if:\n"
    "- Someone directly addresses you (mentions your name or @you)\n"
    "- The conversation topic is relevant to your expertise or role\n"
    "- You can add meaningful value to the ongoing discussion\n"
    "- Any conversation topic you are interested in.\n"
    "If you need more context, use tools to search current session.\n"
    "If the conversation doesn't require your participation, reply only: NO_RESPONSE\n"
    "If you reply, provide your response directly without any prefix.\n"
    "Note: 'You' is a user ID, not you. Your ID is <ASSISTANT>.\n"
)


def _build_agent_profiles() -> dict[str, dict]:
    """Build agent creation profiles for the pool."""
    return {
        "private": dict(include_schedule=True, include_preset_tools=True),
    }


# ── Slash-command system ──────────────────────────────────────────

# handler(server, session_id, provider_id) -> response text
CommandHandler = Callable[["DaemonServer", str, str], Awaitable[str]]


async def _cmd_clear(server: DaemonServer, session_id: str, provider_id: str) -> str:
    """Clear session history and reset agent context."""
    session = server.session_manager.get_or_create(session_id)
    session.messages.clear()
    server.session_manager.save(session)

    await server.agent_pool.reset_context(session_id)

    return "Session cleared."


async def _cmd_stop(server: DaemonServer, session_id: str, provider_id: str) -> str:
    """Interrupt the running agent query for this session."""
    agent = server.agent_pool.get_agent(session_id)
    if not agent:
        return "No running agent to stop."

    try:
        await agent.interrupt()
    except Exception as e:
        return f"Interrupt failed: {e}"

    return "Agent interrupted."


class DaemonServer:
    """Unix Domain Socket server that manages provider connections and agent sessions."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()
        self.is_running = False
        self.gateway = Gateway()
        self.agent_pool: AgentPool | None = None
        self.scheduler: TaskScheduler | None = None
        self._server: asyncio.Server | None = None
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._session_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._max_session_locks = 200
        self.session_manager = SessionManager(get_sessions_dir())
        self._pending_writers: dict[str, asyncio.StreamWriter] = {}

        # Subsystems (initialized in start())
        self._heartbeat: HeartbeatManager | None = None
        self._task_runner: ScheduledTaskRunner | None = None

        # Command registry: command string -> async handler(server, session_id, provider_id)
        self._commands: dict[str, CommandHandler] = {
            "/clear": _cmd_clear,
            "/stop": _cmd_stop,
        }

        self._shutdown_event: asyncio.Event | None = None

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a per-session lock. Evicts oldest when over limit."""
        if session_id in self._session_locks:
            self._session_locks.move_to_end(session_id)
            return self._session_locks[session_id]
        lock = asyncio.Lock()
        self._session_locks[session_id] = lock
        while len(self._session_locks) > self._max_session_locks:
            self._session_locks.popitem(last=False)
        return lock

    async def start(self):
        """Start the daemon server."""
        ensure_config_dir()
        workspace = ensure_workspace_dir()
        logger.info("Workspace: %s", workspace)

        from vtuber.onboarding import create_default_configs
        create_default_configs()
        migrate_config()

        # Remove old socket
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Initialize scheduler
        db_path = get_db_path()
        self.scheduler = TaskScheduler(db_path)
        set_task_queue(self._task_queue)
        set_scheduler(self.scheduler)
        self.scheduler.start()

        # Initialize agent pool (agents are created lazily on first message)
        config = get_config()
        self.agent_pool = AgentPool(
            max_agents=config.max_agents,
            profiles=_build_agent_profiles(),
        )
        logger.info("Agent pool initialized (max=%d)", config.max_agents)

        # Start scheduled task runner (needs agent to be ready)
        self._task_runner = ScheduledTaskRunner(self, self._task_queue)
        self._task_runner.start()
        logger.info("Scheduler started (db=%s)", db_path)

        # Start Unix socket server
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        self.is_running = True

        # Start heartbeat
        self._heartbeat = HeartbeatManager(
            self.gateway, config.heartbeat_interval,
        )
        self._heartbeat.start()

        # Write PID file
        pid_path = get_pid_path()
        pid_path.write_text(str(os.getpid()))

        logger.info(
            "Daemon started on %s (pid=%d)",
            self.socket_path, os.getpid(),
        )

        # Setup signal handlers — schedule shutdown via event
        self._shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown_event.set)

    # ── Client handling ───────────────────────────────────────────

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ):
        """Handle a new client connection."""
        addr = writer.get_extra_info("peername") or "client"
        provider_id: str | None = None
        self._pending_writers[id(writer)] = writer

        try:
            buffer = b""
            while self.is_running:
                try:
                    data = await reader.read(4096)
                    if not data:
                        break

                    buffer += data
                    if len(buffer) > _MAX_BUFFER_SIZE:
                        logger.warning("Client buffer exceeded %d bytes, disconnecting", _MAX_BUFFER_SIZE)
                        break
                    while b"\n" in buffer:
                        raw_line, buffer = buffer.split(b"\n", 1)
                        line = raw_line.decode("utf-8", errors="replace")
                        if line.strip():
                            await self._process_message(line, writer, provider_id)
                            # Check if registration happened
                            if provider_id is None:
                                for pid, conn in self.gateway.connections.items():
                                    if conn.writer is writer:
                                        provider_id = pid
                                        break
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Error reading from client: %s", e)
                    break
        finally:
            self._pending_writers.pop(id(writer), None)
            if provider_id:
                await self.gateway.unregister(provider_id)
            else:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                logger.info("Unregistered client disconnected: %s", addr)

    async def _process_message(
        self, line: str, writer: asyncio.StreamWriter, provider_id: str | None,
    ):
        """Route an incoming message to the appropriate handler."""
        try:
            msg = decode_message(line)
            msg_type = msg.get("type")

            if msg_type == MessageType.REGISTER:
                conn = ProviderConnection(
                    provider_type=msg.get("provider", "unknown"),
                    provider_id=msg.get("provider_id", f"anon-{id(writer)}"),
                    writer=writer,
                )
                await self.gateway.register(conn)

            elif msg_type == MessageType.USER_MESSAGE:
                content = msg.get("content", "")
                sender = msg.get("sender", "owner")
                is_owner = msg.get("is_owner", True)
                is_private = msg.get("is_private", True)
                should_reply = msg.get("should_reply", True)
                channel_id = msg.get("channel_id")
                session_id = msg.get("session_id")
                pid = provider_id or msg.get("provider_id")

                if not pid:
                    logger.warning("User message from unregistered provider, ignoring")
                else:
                    session_id = session_id or (
                        f"dm:{pid}:{sender}" if is_private
                        else f"group:{channel_id or 'unknown'}"
                    )

                    # Slash-command interception (exact match)
                    handler = self._commands.get(content.strip()) if content else None
                    if handler:
                        reply = await handler(self, session_id, pid)
                        await self.gateway.send_to(pid, {
                            "type": MessageType.ASSISTANT_MESSAGE,
                            "step": 0,
                            "content": reply,
                            "done": True,
                            "session_id": session_id,
                        })
                    else:
                        # Record to session (skip empty flush)
                        if content:
                            session = self.session_manager.get_or_create(session_id)
                            session.add_message("user", content, sender=sender, is_owner=is_owner)
                            self.session_manager.save(session)

                        if should_reply:
                            if is_private:
                                coro = self._handle_private_message(
                                    content, pid, sender, is_owner,
                                    session_id=session_id,
                                )
                            else:
                                coro = self._handle_group_message(
                                    content, pid, sender, is_owner,
                                    channel_id=channel_id,
                                    session_id=session_id,
                                )
                            asyncio.create_task(coro)

            elif msg_type == MessageType.PING:
                response = encode_message({"type": MessageType.PONG})
                writer.write(response.encode("utf-8"))
                await writer.drain()

            elif msg_type == MessageType.RELOAD:
                asyncio.create_task(self._handle_reload(writer))

            else:
                logger.warning("Unknown message type: %s", msg_type)

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON message: %s", e)
        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)

    # ── Message handlers ──────────────────────────────────────────

    async def _dispatch_to_agent(
        self,
        query: str,
        provider_id: str,
        session_id: str,
        log_source: str,
        *,
        profile: str = "private",
        no_response_token: str | None = None,
        notify_heartbeat: bool = False,
    ):
        """Shared message handler: acquire agent, run query, handle errors."""
        agent = None
        try:
            agent = await self.agent_pool.get(session_id, profile=profile)
            lock = self._get_session_lock(session_id)
            async with lock:
                await self._run_agent_query(
                    agent, query, provider_id, session_id, log_source,
                    no_response_token=no_response_token,
                )
            if notify_heartbeat and self._heartbeat:
                self._heartbeat.on_message()
            # Check if the agent requested a self-restart
            if consume_restart():
                logger.info("[%s] agent requested restart — recreating", log_source)
                await self.agent_pool.kill_and_recreate(session_id, profile=profile)
        except AgentTimeoutError as e:
            logger.error("[%s] timeout: %s — recovering agent", log_source, e)
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": "Agent response timeout, recovering...",
            })
            await self.agent_pool.kill_and_recreate(session_id, profile=profile)
        except Exception as e:
            logger.error("[%s] error: %s", log_source, e, exc_info=True)
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": str(e),
            })
        finally:
            # If agent was evicted from pool during query (e.g. /clear),
            # we own the last reference — clean it up to avoid subprocess leak.
            if agent and not self.agent_pool.owns(session_id, agent):
                await safe_disconnect(agent)

    async def _handle_private_message(
        self, content: str, provider_id: str, sender: str, is_owner: bool,
        *, session_id: str,
    ):
        """Handle a private/DM message — routes to a per-session agent."""
        logger.debug("[%s] %s", sender, truncate(content))

        query_content = content if is_owner else f"[{sender}]: {content}"
        await self._dispatch_to_agent(
            query_content, provider_id, session_id, "agent",
            notify_heartbeat=True,
        )

    async def _handle_group_message(
        self,
        content: str,
        provider_id: str,
        sender: str,
        is_owner: bool,
        *,
        channel_id: str | None = None,
        session_id: str,
    ):
        """Handle a group chat message — one-shot query with NO_RESPONSE support."""
        channel_label = channel_id or "unknown"
        log_source = f"group/{channel_label}"

        # Build context from session history
        session = self.session_manager.get_or_create(session_id)
        limit = get_config().group_context_limit
        # For flush (content=""), all context comes from history
        # For normal messages, the current message was already appended
        tail = limit + 1 if content else limit
        context_msgs = session.messages[-tail:-1] if content and len(session.messages) > 1 else session.messages[-limit:]

        def _fmt_sender(msg: dict) -> str:
            """Format sender label, annotating the owner."""
            name = msg.get("sender", msg.get("role", "?"))
            if msg.get("is_owner"):
                return f"{name}<OWNER>"
            return name

        query_parts = []
        if context_msgs:
            query_parts.append("[group chat context]")
            for msg in context_msgs:
                query_parts.append(f"{_fmt_sender(msg)}: {msg.get('content', '')}")

        if content:
            owner_tag = "<OWNER>" if is_owner else ""
            query_parts.append(f"{sender}{owner_tag}: {content}")
        query_text = "\n".join(query_parts)

        logger.debug("[%s] %s: %s", log_source, sender, truncate(query_text))
        
        # Resolve owner name: current sender > context history > session metadata
        owner_name = None
        if is_owner:
            owner_name = sender
            # Persist to session metadata so we know even when owner is silent
            if session.metadata.get("owner_name") != sender:
                session.metadata["owner_name"] = sender
                self.session_manager.save(session)
        else:
            for msg in context_msgs:
                if msg.get("is_owner"):
                    owner_name = msg.get("sender")
                    break
            if not owner_name:
                owner_name = session.metadata.get("owner_name", "<OWNER>")
        group_instruction = GROUP_INSTRUCTION.format(owner_name=owner_name)

        # One-shot query — no persistent agent needed for group chat
        # Security: only the owner gets preset tools (Bash, Read, Write…).
        # Other group members get conversational + MCP tools only.
        options = build_agent_options(
            prompt_suffix=group_instruction,
            include_preset_tools=is_owner,
        )
        await self._run_oneshot_query(
            query_text, options, provider_id, session_id, log_source,
            no_response_token="NO_RESPONSE",
        )

    async def _handle_reload(self, writer: asyncio.StreamWriter) -> None:
        """Reload agents with fresh prompts (hot-reload)."""
        logger.info("Reload requested — killing all agents and resetting config")
        try:
            reset_config()
            self.agent_pool.kill_all_and_clear()

            # Reinitialize pool with fresh config
            config = get_config()
            self.agent_pool = AgentPool(
                max_agents=config.max_agents,
                profiles=_build_agent_profiles(),
            )

            logger.info("Reload complete — agent pool reset (max=%d)", config.max_agents)
            response = encode_message({
                "type": MessageType.PONG,
                "message": "reload ok",
            })
        except Exception as e:
            logger.error("Reload failed: %s", e, exc_info=True)
            response = encode_message({
                "type": MessageType.ERROR,
                "content": f"reload failed: {e}",
            })

        try:
            writer.write(response.encode("utf-8"))
            await writer.drain()
        except Exception:
            pass

    async def _stream_events_to_provider(
        self,
        events: "AsyncIterator[AgentEvent]",
        provider_id: str,
        session_id: str,
        log_source: str,
        *,
        no_response_token: str | None = None,
    ):
        """Forward agent events to a provider, guarantee done signal, and record session.

        Shared by both persistent agent queries and one-shot (ephemeral) queries.
        """
        step = 0
        full_text = ""
        done_sent = False

        try:
            async for event in events:
                if event.type == "tool":
                    await self.gateway.send_to(provider_id, {
                        "type": MessageType.PROGRESS,
                        "tool": event.tool,
                        "session_id": session_id,
                    })
                elif event.type == "text":
                    full_text += event.text
                    await self.gateway.send_to(provider_id, {
                        "type": MessageType.ASSISTANT_MESSAGE,
                        "step": step,
                        "content": event.text,
                        "done": False,
                        "session_id": session_id,
                    })
                    step += 1
                elif event.type == "result":
                    is_no_response = (
                        no_response_token
                        and no_response_token in full_text.strip().upper()
                    )
                    final_msg = {
                        "type": MessageType.ASSISTANT_MESSAGE,
                        "step": step,
                        "content": "",
                        "done": True,
                        "session_id": session_id,
                    }
                    if is_no_response:
                        logger.debug("[%s] agent chose not to respond", log_source)
                        final_msg["no_response"] = True
                    await self.gateway.send_to(provider_id, final_msg)
                    done_sent = True
        finally:
            if not done_sent:
                logger.debug("[%s] sending fallback done signal", log_source)
                await self.gateway.send_to(provider_id, {
                    "type": MessageType.ASSISTANT_MESSAGE,
                    "step": step,
                    "content": "",
                    "done": True,
                    "session_id": session_id,
                })

        if full_text.strip():
            is_no_response = (
                no_response_token
                and no_response_token in full_text.strip().upper()
            )
            if not is_no_response:
                session = self.session_manager.get_or_create(session_id)
                session.add_message("<ASSISTANT>", full_text.strip())
                self.session_manager.save(session)

    async def _run_agent_query(
        self,
        agent: ClaudeSDKClient,
        query: str,
        provider_id: str,
        session_id: str,
        log_source: str,
        *,
        no_response_token: str | None = None,
    ):
        """Run a persistent agent query and forward events to the provider."""
        events = iter_response(
            agent, query, session_id=session_id, log_source=log_source,
        )
        await self._stream_events_to_provider(
            events, provider_id, session_id, log_source,
            no_response_token=no_response_token,
        )

    async def _run_oneshot_query(
        self,
        query: str,
        options: "ClaudeAgentOptions",
        provider_id: str,
        session_id: str,
        log_source: str,
        *,
        no_response_token: str | None = None,
    ):
        """Run a one-shot (ephemeral) query and forward events to the provider."""
        try:
            events = iter_oneshot(query, options, log_source=log_source)
            await self._stream_events_to_provider(
                events, provider_id, session_id, log_source,
                no_response_token=no_response_token,
            )
        except Exception as e:
            logger.error("[%s] oneshot error: %s", log_source, e, exc_info=True)
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": str(e),
            })

    # ── Lifecycle ─────────────────────────────────────────────────

    async def shutdown(self):
        """Shutdown the daemon server gracefully."""
        if not self.is_running:
            return
        logger.info("Shutting down daemon...")
        self.is_running = False

        # Stop subsystems
        if self._heartbeat:
            await self._heartbeat.stop()
        if self._task_runner:
            await self._task_runner.stop()

        # Disconnect all agents
        if self.agent_pool:
            try:
                await self.agent_pool.close_all()
            except Exception as e:
                logger.warning("Error closing agent pool: %s", e)

        # Close provider connections
        try:
            await self.gateway.close_all()
        except Exception as e:
            logger.warning("Error closing gateway: %s", e)

        # Shutdown scheduler
        if self.scheduler:
            try:
                self.scheduler.shutdown()
            except Exception as e:
                logger.warning("Error shutting down scheduler: %s", e)

        # Close socket server
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Remove socket and PID files
        if self.socket_path.exists():
            self.socket_path.unlink()
        pid_path = get_pid_path()
        if pid_path.exists():
            pid_path.unlink()

        logger.info("Daemon shutdown complete")

    async def run_forever(self):
        """Run the server until shutdown."""
        await self.start()
        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()


# ── Daemon CLI helpers ────────────────────────────────────────────


def main():
    """Main entry point for daemon server."""
    from vtuber.daemon.cli import setup_logging

    setup_logging()
    try:
        server = DaemonServer()
        asyncio.run(server.run_forever())
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error("Daemon error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
