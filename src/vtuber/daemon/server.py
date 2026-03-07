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
)
from vtuber.daemon.agents import GroupAgentManager, create_agent, safe_disconnect
from vtuber.daemon.gateway import Gateway, ProviderConnection
from vtuber.daemon.heartbeat import HeartbeatManager
from vtuber.daemon.protocol import MessageType, decode_message, encode_message
from vtuber.daemon.scheduler import TaskScheduler
from vtuber.daemon.streaming import AgentTimeoutError, iter_response, kill_agent_process, truncate
from vtuber.daemon.tasks import ScheduledTaskRunner
from vtuber.tools.memory import create_session_id, log_message
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
        self.agent: ClaudeSDKClient | None = None
        self.scheduler: TaskScheduler | None = None
        self._server: asyncio.Server | None = None
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._agent_lock = asyncio.Lock()
        self.group_agents = GroupAgentManager()
        self.session_id = create_session_id()
        self._pending_writers: dict[str, asyncio.StreamWriter] = {}

        # Subsystems (initialized in start())
        self._heartbeat: HeartbeatManager | None = None
        self._task_runner: ScheduledTaskRunner | None = None

    async def start(self):
        """Start the daemon server."""
        ensure_config_dir()
        workspace = ensure_workspace_dir()
        logger.info("Workspace: %s", workspace)

        from vtuber.onboarding import create_default_configs
        create_default_configs()

        # Remove old socket
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Initialize scheduler
        db_path = get_db_path()
        self.scheduler = TaskScheduler(db_path)
        set_task_queue(self._task_queue)
        set_scheduler(self.scheduler)
        self.scheduler.start()

        # Start scheduled task runner
        self._task_runner = ScheduledTaskRunner(self.gateway, self._task_queue)
        self._task_runner.start()
        logger.info("Scheduler started (db=%s)", db_path)

        # Initialize main agent
        self.agent = await create_agent(
            include_schedule=True,
            include_preset_tools=True,
        )
        logger.info("Agent initialized")

        # Start Unix socket server
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        self.is_running = True

        # Start heartbeat
        config = get_config()
        self._heartbeat = HeartbeatManager(
            self.gateway, self.session_id, config.heartbeat_interval,
        )
        self._heartbeat.start()

        # Write PID file
        pid_path = get_pid_path()
        pid_path.write_text(str(os.getpid()))

        logger.info(
            "Daemon started on %s (pid=%d, session=%s)",
            self.socket_path, os.getpid(), self.session_id,
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
                pid = provider_id or msg.get("provider_id")

                if not pid:
                    logger.warning("User message from unregistered provider, ignoring")
                elif is_private:
                    await self._handle_private_message(content, pid, sender, is_owner)
                else:
                    await self._handle_group_message(
                        content, pid, sender, is_owner,
                        channel_id=channel_id, context=context,
                    )

            elif msg_type == MessageType.PING:
                response = encode_message({"type": MessageType.PONG})
                writer.write(response.encode("utf-8"))
                await writer.drain()

            else:
                logger.warning("Unknown message type: %s", msg_type)

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON message: %s", e)
        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)

    # ── Message handlers ──────────────────────────────────────────

    async def _handle_private_message(
        self, content: str, provider_id: str, sender: str, is_owner: bool,
    ):
        """Handle a private/DM message — routes to the main agent."""
        if not self.agent:
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": "Agent not initialized",
            })
            return

        log_message(self.session_id, "user", content, sender=sender)
        logger.debug("[%s] %s", sender, truncate(content))

        query_content = content if is_owner else f"[{sender}]: {content}"

        try:
            logger.debug("Acquiring agent lock...")
            async with self._agent_lock:
                logger.debug("Agent lock acquired, sending query")
                await self._stream_agent_response(
                    self.agent, query_content, provider_id, "agent",
                )
            if self._heartbeat:
                self._heartbeat.on_message()
        except AgentTimeoutError as e:
            logger.error("[agent] timeout: %s — recovering agent", e)
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": "Agent 响应超时，正在恢复...",
            })
            await self._recover_agent()
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
    ):
        """Handle a group chat message — routes to a per-channel persistent agent."""
        channel_label = channel_id or "unknown"
        logger.debug("[group/%s] %s: %s", channel_label, sender, truncate(content))

        query_parts = []
        if context:
            query_parts.append("[群聊上下文]")
            for msg in context:
                query_parts.append(f"{msg.get('sender', '?')}: {msg.get('content', '')}")
            query_parts.append("")
        query_parts.append(f"[{sender}]: {content}")
        query_text = "\n".join(query_parts)

        try:
            agent = await self.group_agents.get_or_create(channel_label)
            lock = self.group_agents.get_lock(channel_label)

            async with lock:
                await self._stream_agent_response(
                    agent, query_text, provider_id, f"group/{channel_label}",
                    no_response_token="NO_RESPONSE",
                )
        except AgentTimeoutError as e:
            logger.error("[group/%s] timeout: %s — recovering agent", channel_label, e)
            await self.group_agents.recover(channel_label)
        except Exception as e:
            logger.error("[group/%s] error: %s", channel_label, e, exc_info=True)
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": str(e),
            })

    async def _recover_agent(self) -> None:
        """Recover from a hung agent by killing its subprocess and creating a new one."""
        logger.warning("Recovering agent — killing old subprocess")
        if self.agent:
            kill_agent_process(self.agent)
            await safe_disconnect(self.agent)
            self.agent = None

        self.agent = await create_agent(
            include_schedule=True,
            include_preset_tools=True,
        )
        logger.info("Agent recovered (new session)")

    async def _stream_agent_response(
        self,
        agent: ClaudeSDKClient,
        query: str,
        provider_id: str,
        log_source: str,
        *,
        no_response_token: str | None = None,
    ):
        """Send a query to an agent and stream the response to a provider.

        Guarantees that a done=True message is always sent, even on error.
        """
        stream_id = f"stream_{provider_id}"
        index = 0
        assistant_text = ""
        done_sent = False

        try:
            async for event in iter_response(agent, query, log_source=log_source):
                if event.type == "tool":
                    await self.gateway.send_to(provider_id, {
                        "type": MessageType.PROGRESS,
                        "tool": event.tool,
                    })
                elif event.type == "text":
                    assistant_text += event.text
                    await self.gateway.send_to(provider_id, {
                        "type": MessageType.ASSISTANT_MESSAGE,
                        "stream_id": stream_id,
                        "index": index,
                        "content": event.text,
                        "done": False,
                    })
                    index += 1
                elif event.type == "result":
                    is_no_response = (
                        no_response_token
                        and no_response_token in assistant_text.strip().upper()
                    )
                    final_msg = {
                        "type": MessageType.ASSISTANT_MESSAGE,
                        "stream_id": stream_id,
                        "index": index,
                        "content": "",
                        "done": True,
                    }
                    if is_no_response:
                        logger.debug("[%s] agent chose not to respond", log_source)
                        final_msg["no_response"] = True
                    await self.gateway.send_to(provider_id, final_msg)
                    done_sent = True
        finally:
            # Always send done=True so the client never hangs waiting for it.
            if not done_sent:
                logger.debug("[%s] sending fallback done signal", log_source)
                await self.gateway.send_to(provider_id, {
                    "type": MessageType.ASSISTANT_MESSAGE,
                    "stream_id": stream_id,
                    "index": index,
                    "content": "",
                    "done": True,
                })

        if assistant_text.strip():
            is_no_response = (
                no_response_token
                and no_response_token in assistant_text.strip().upper()
            )
            if not is_no_response:
                log_message(self.session_id, "assistant", assistant_text.strip())

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

        # Disconnect agents
        if self.agent:
            try:
                await self.agent.disconnect()
            except Exception:
                pass
        await self.group_agents.close_all()

        # Close provider connections
        await self.gateway.close_all()

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
