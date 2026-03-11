"""Unix Domain Socket server for daemon."""

import asyncio
import json
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

from claude_agent_sdk import ClaudeSDKClient

from vtuber.config import (
    ensure_config_dir,
    ensure_workspace_dir,
    get_config,
    get_db_path,
    get_log_path,
    get_pid_path,
    get_socket_path,
    get_sessions_dir,
    migrate_config,
    reset_config,
)
from vtuber.daemon.agents import AgentPool
from vtuber.daemon.gateway import Gateway, ProviderConnection
from vtuber.daemon.heartbeat import HeartbeatManager
from vtuber.daemon.protocol import MessageType, decode_message, encode_message
from vtuber.daemon.scheduler import TaskScheduler
from vtuber.daemon.agent_query import AgentTimeoutError, iter_response, truncate
from vtuber.daemon.tasks import ScheduledTaskRunner
from vtuber.tools.memory import SessionManager
from vtuber.tools.schedule import set_scheduler, set_task_queue

logger = logging.getLogger("vtuber.daemon")


def setup_logging():
    """Configure logging to ~/.vtuber/daemon.log with rotation."""
    ensure_config_dir()
    log_path = get_log_path()

    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger("vtuber")
    level = getattr(logging, get_config().log_level, logging.INFO)
    root.setLevel(level)
    root.addHandler(handler)

    # Also log to stderr when running in foreground
    if sys.stderr and sys.stderr.isatty():
        console_handler = RichHandler(
            rich_tracebacks=True,
            show_path=False,
            markup=True,
        )
        console_handler.setLevel(level)
        root.addHandler(console_handler)


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
        self._session_locks: dict[str, asyncio.Lock] = {}
        self.session_manager = SessionManager(get_sessions_dir())
        self._pending_writers: dict[str, asyncio.StreamWriter] = {}

        # Subsystems (initialized in start())
        self._heartbeat: HeartbeatManager | None = None
        self._task_runner: ScheduledTaskRunner | None = None

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a per-session lock for concurrent session isolation."""
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

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
            include_schedule=True,
            include_preset_tools=True,
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

        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

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
                channel_id = msg.get("channel_id")
                context = msg.get("context")
                session_id = msg.get("session_id")
                pid = provider_id or msg.get("provider_id")

                if not pid:
                    logger.warning("User message from unregistered provider, ignoring")
                elif is_private:
                    await self._handle_private_message(
                        content, pid, sender, is_owner,
                        session_id=session_id,
                    )
                else:
                    await self._handle_group_message(
                        content, pid, sender, is_owner,
                        channel_id=channel_id, context=context,
                        session_id=session_id,
                    )

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

    async def _handle_private_message(
        self, content: str, provider_id: str, sender: str, is_owner: bool,
        *, session_id: str | None = None,
    ):
        """Handle a private/DM message — routes to a per-session agent."""
        # Use explicit session_id, or derive from provider + sender.
        # Each distinct sender gets their own session even within the same provider.
        session_id = session_id or f"dm:{provider_id}:{sender}"

        session = self.session_manager.get_or_create(session_id)
        session.add_message("user", content, sender=sender)
        self.session_manager.save(session)
        logger.debug("[%s] %s", sender, truncate(content))

        query_content = content if is_owner else f"[{sender}]: {content}"

        try:
            agent = await self.agent_pool.get(session_id)
            lock = self._get_session_lock(session_id)
            async with lock:
                await self._run_agent_query(
                    agent, query_content, provider_id, session_id, "agent",
                )
            if self._heartbeat:
                self._heartbeat.on_message()
        except AgentTimeoutError as e:
            logger.error("[agent] timeout: %s — recovering agent", e)
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": "Agent 响应超时，正在恢复...",
            })
            await self.agent_pool.kill_and_recreate(session_id)
        except Exception as e:
            logger.error("[agent] error handling message: %s", e, exc_info=True)
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": str(e),
            })

    async def _handle_group_message(
        self,
        content: str,
        provider_id: str,
        sender: str,
        is_owner: bool,  # noqa: ARG002
        *,
        channel_id: str | None = None,
        context: list[dict] | None = None,
        session_id: str | None = None,
    ):
        """Handle a group chat message — routes to a per-channel agent."""
        channel_label = channel_id or "unknown"
        # Use explicit session_id, or derive from channel.
        # Each distinct channel gets its own session.
        session_id = session_id or f"group:{channel_label}"
        logger.debug("[group/%s] %s: %s", channel_label, sender, truncate(content))

        query_parts = []
        if context:
            query_parts.append("[群聊上下文]")
            for msg in context:
                query_parts.append(f"{msg.get('sender', '?')}: {msg.get('content', '')}")
            query_parts.append("")
        query_parts.append(f"[{sender}]: {content}")
        query_text = "\n".join(query_parts)

        # Prepend group chat instruction for this session
        group_instruction = (
            f"你正在参与一个群聊（频道: {channel_label}）。\n"
            "你会收到群里最近的对话消息。请根据对话内容决定是否需要回复。\n"
            "如果对话不需要你参与，请只回复: NO_RESPONSE\n"
            "如果需要回复，直接回复内容即可，不要加任何前缀。\n\n"
        )

        try:
            agent = await self.agent_pool.get(session_id)
            lock = self._get_session_lock(session_id)
            async with lock:
                await self._run_agent_query(
                    agent, group_instruction + query_text, provider_id, session_id,
                    f"group/{channel_label}",
                    no_response_token="NO_RESPONSE",
                )
        except AgentTimeoutError as e:
            logger.error("[group/%s] timeout: %s — recovering agent", channel_label, e)
            await self.agent_pool.kill_and_recreate(session_id)
        except Exception as e:
            logger.error("[group/%s] error: %s", channel_label, e, exc_info=True)
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": str(e),
            })

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
                include_schedule=True,
                include_preset_tools=True,
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
        """Run an agent query and forward each step to the provider.

        A single query may produce multiple steps: text messages,
        tool-use progress, and a final done signal.  Each text step
        is sent as a complete, independent assistant_message.

        Guarantees that a done=True message is always sent, even on error.
        """

        step = 0
        full_text = ""
        done_sent = False

        try:
            async for event in iter_response(
                agent, query, session_id=session_id, log_source=log_source,
            ):
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
                session.add_message("assistant", full_text.strip())
                self.session_manager.save(session)

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
            except Exception:
                pass

        # Close provider connections
        try:
            await self.gateway.close_all()
        except BaseException:
            pass

        # Shutdown scheduler
        if self.scheduler:
            try:
                self.scheduler.shutdown()
            except Exception:
                pass

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
            while self.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()


# ── Daemon CLI helpers ────────────────────────────────────────────


def start_daemon_background():
    """Start the daemon in background mode."""
    import subprocess

    socket_path = get_socket_path()
    pid_path = get_pid_path()

    # Check if daemon is already running
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
            print(f"Daemon is already running (PID: {pid})")
            return
        except (OSError, ProcessLookupError):
            pid_path.unlink()
            if socket_path.exists():
                socket_path.unlink()

    # Run onboarding interactively before starting background daemon
    from vtuber.onboarding import check_and_run_onboarding

    try:
        onboarded = asyncio.run(check_and_run_onboarding())
        if onboarded:
            print("Onboarding completed")
    except Exception as e:
        print(f"Onboarding check failed: {e}")
        print("Continuing with default configuration...")
        from vtuber.onboarding import create_default_configs
        create_default_configs()

    # Start daemon in background
    try:
        ensure_config_dir()
        log_path = get_log_path()
        log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115

        subprocess.Popen(
            [sys.executable, "-m", "vtuber.daemon.server"],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        log_file.close()
        print("Daemon started in background")
        print(f"Log: {log_path}")

        import time
        time.sleep(1)

        if pid_path.exists():
            pid = int(pid_path.read_text().strip())
            print(f"Daemon running with PID: {pid}")
        else:
            print("Warning: Daemon may have failed to start (no PID file)")

    except Exception as e:
        print(f"Error starting daemon: {e}")
        sys.exit(1)


def stop_daemon():
    """Stop the running daemon."""
    pid_path = get_pid_path()

    if not pid_path.exists():
        print("Daemon is not running (no PID file)")
        return

    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGTERM)

        import time
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(1)
            except ProcessLookupError:
                print(f"Daemon stopped (PID: {pid})")
                return

        print("Daemon did not stop gracefully, forcing...")
        os.kill(pid, signal.SIGKILL)
        print(f"Daemon killed (PID: {pid})")

    except ProcessLookupError:
        print("Daemon is not running (process not found)")
        pid_path.unlink()
    except Exception as e:
        print(f"Error stopping daemon: {e}")


def check_status():
    """Check if the daemon is running."""
    socket_path = get_socket_path()
    pid_path = get_pid_path()

    if not pid_path.exists():
        print("Daemon is not running (no PID file)")
        return False

    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)

        print(f"Daemon is running (PID: {pid})")
        print(f"Socket: {socket_path}")

        if socket_path.exists():
            print("Socket file exists")
            import socket as sock
            try:
                test_sock = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
                test_sock.connect(str(socket_path))
                test_sock.close()
                print("Socket connection: OK")
                return True
            except Exception as e:
                print(f"Socket connection: FAILED ({e})")
                return False
        else:
            print("Socket file: MISSING")
            return False

    except ProcessLookupError:
        print("Daemon is not running (process not found)")
        pid_path.unlink()
        return False
    except Exception as e:
        print(f"Error checking status: {e}")
        return False


def main():
    """Main entry point for daemon server."""
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
