"""Unix Domain Socket server for daemon."""

import asyncio
import json
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
)

from vtuber.daemon.gateway import Gateway, ProviderConnection
from vtuber.daemon.protocol import decode_message, encode_message, MessageType
from vtuber.daemon.scheduler import TaskScheduler
from vtuber.persona import build_system_prompt
from vtuber.config import (
    ensure_config_dir,
    get_socket_path,
    get_pid_path,
    get_db_path,
    get_persona_path,
    get_user_path,
    get_heartbeat_path,
    get_log_path,
)
from vtuber.tools.schedule import set_scheduler, set_task_queue
from vtuber.tools.memory import log_message, create_session_id
from vtuber.utils import extract_stream_text, extract_tool_use_start

logger = logging.getLogger("vtuber.daemon")

CLI_PATH = "claude"


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
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)

    # Also log to stderr when running in foreground
    if sys.stderr and sys.stderr.isatty():
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(
            logging.Formatter("[%(levelname)s] %(message)s")
        )
        console_handler.setLevel(logging.INFO)
        root.addHandler(console_handler)


def _truncate(s: str, max_len: int = 200) -> str:
    """Truncate a string for log display."""
    s = s.replace("\n", " ").strip()
    return s[:max_len] + "..." if len(s) > max_len else s


def _log_stream_event(msg, source: str = "agent"):
    """Log interesting stream events (tool calls, results, errors)."""
    if isinstance(msg, StreamEvent):
        event = msg.event
        etype = event.get("type")

        if etype == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                tool_name = block.get("name", "?")
                logger.info("[%s] tool_call: %s", source, tool_name)

    elif isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                input_preview = _truncate(json.dumps(block.input, ensure_ascii=False))
                logger.info("[%s] tool_call: %s(%s)", source, block.name, input_preview)
            elif isinstance(block, TextBlock) and block.text:
                logger.debug("[%s] text: %s", source, _truncate(block.text))

    elif isinstance(msg, ResultMessage):
        cost = f"${msg.total_cost_usd:.4f}" if msg.total_cost_usd else "n/a"
        logger.info(
            "[%s] result: turns=%d, cost=%s, duration=%dms",
            source, msg.num_turns, cost, msg.duration_ms,
        )


def _create_tools_server(include_schedule: bool = True):
    """Create an SDK MCP server with vtuber tools."""
    from vtuber.tools.memory import search_sessions, list_sessions, read_session

    tools = [search_sessions, list_sessions, read_session]
    allowed = ["search_sessions", "list_sessions", "read_session"]

    if include_schedule:
        from vtuber.tools.schedule import schedule_create, schedule_list, schedule_cancel

        tools.extend([schedule_create, schedule_list, schedule_cancel])
        allowed.extend(["schedule_create", "schedule_list", "schedule_cancel"])

    server = create_sdk_mcp_server("vtuber-tools", tools=tools)
    return server, allowed


class DaemonServer:
    """Unix Domain Socket server that manages provider connections and agent sessions."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()
        self.is_running = False
        self.gateway: Gateway = Gateway()
        self.agent: ClaudeSDKClient | None = None
        self.scheduler: TaskScheduler | None = None
        self.conversation_history: list[dict[str, Any]] = []
        self._server: asyncio.Server | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._task_consumer: asyncio.Task | None = None
        self._agent_lock: asyncio.Lock = asyncio.Lock()
        self.heartbeat_interval: int = 5  # Minutes between heartbeats
        self.session_id: str = create_session_id()
        # Track which provider_id initiated the current agent request
        self._pending_writers: dict[str, asyncio.StreamWriter] = {}

    async def start(self):
        """Start the daemon server."""
        # Ensure config directory exists
        ensure_config_dir()

        # Ensure config files exist (use defaults if onboarding wasn't run)
        from vtuber.onboarding import create_default_configs

        create_default_configs()

        # Remove old socket if exists
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Initialize scheduler
        db_path = get_db_path()
        self.scheduler = TaskScheduler(db_path)

        # Wire up task queue before starting scheduler
        set_task_queue(self._task_queue)
        set_scheduler(self.scheduler)
        self.scheduler.start()

        # Start queue consumer for scheduled tasks
        self._task_consumer = asyncio.create_task(self._process_scheduled_tasks())
        logger.info("Scheduler started (db=%s)", db_path)

        # Initialize agent
        await self._initialize_agent()
        logger.info("Agent initialized")

        # Start Unix socket server
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        self.is_running = True

        # Start heartbeat timer after is_running is set so the loop condition works
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("Heartbeat started (every %d min)", self.heartbeat_interval)

        # Write PID file
        pid_path = get_pid_path()
        pid_path.write_text(str(os.getpid()))

        logger.info("Daemon started on %s (pid=%d, session=%s)",
                     self.socket_path, os.getpid(), self.session_id)

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

    async def _process_scheduled_tasks(self):
        """Consume scheduled tasks from the queue and execute them."""
        try:
            while True:
                task_prompt = await self._task_queue.get()
                try:
                    await self._execute_scheduled_task(task_prompt)
                except Exception as e:
                    logger.error("Scheduled task error: %s", e, exc_info=True)
                finally:
                    self._task_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _execute_scheduled_task(self, task_prompt: str):
        """Execute a scheduled task using a temporary subagent."""
        logger.info("[schedule] executing: %s", _truncate(task_prompt))

        # Create a temporary subagent with independent context
        try:
            persona_path = get_persona_path()
            user_path = get_user_path()
            system_prompt = build_system_prompt(persona_path, user_path)

            # Create subagent with memory tools only
            tools_server, allowed_tools = _create_tools_server(include_schedule=False)
            options = ClaudeAgentOptions(
                system_prompt=f"{system_prompt}\n\nYou are executing a scheduled task. Respond concisely.",
                mcp_servers={"vtuber-tools": tools_server},
                allowed_tools=allowed_tools,
                permission_mode="bypassPermissions",
                cli_path=CLI_PATH,
            )
            subagent = ClaudeSDKClient(options)
            await subagent.connect()

            # Execute task
            stream_id = f"task_{id(subagent)}"
            index = 0

            await subagent.query(f"[Scheduled Task] {task_prompt}")
            async for msg in subagent.receive_response():
                _log_stream_event(msg, "schedule")

                tool_name = extract_tool_use_start(msg)
                if tool_name:
                    await self.gateway.broadcast({
                        "type": MessageType.PROGRESS,
                        "tool": tool_name,
                    })
                    continue

                text = extract_stream_text(msg)
                if text:
                    # Stream result to all providers
                    await self.gateway.broadcast(
                        {
                            "type": MessageType.TASK_MESSAGE,
                            "stream_id": stream_id,
                            "index": index,
                            "content": text,
                            "task": task_prompt,
                            "is_final": False,
                        }
                    )
                    index += 1

                elif isinstance(msg, ResultMessage):
                    # Send final message
                    await self.gateway.broadcast(
                        {
                            "type": MessageType.TASK_MESSAGE,
                            "stream_id": stream_id,
                            "index": index,
                            "content": "",
                            "task": task_prompt,
                            "is_final": True,
                        }
                    )

            await subagent.disconnect()
            logger.info("[schedule] completed: %s", _truncate(task_prompt))

        except Exception as e:
            logger.error("[schedule] failed: %s — %s", _truncate(task_prompt), e, exc_info=True)
            # Notify providers of error
            await self.gateway.broadcast(
                {
                    "type": MessageType.ERROR,
                    "content": f"Error executing task '{task_prompt}': {str(e)}",
                    "is_final": True,
                }
            )

    async def _initialize_agent(self):
        """Initialize the main Claude SDK client with persona and user profile."""
        system_prompt = build_system_prompt(get_persona_path(), get_user_path())

        # Create SDK MCP server with all tools
        tools_server, allowed_tools = _create_tools_server(include_schedule=True)

        # Create agent with all Claude Code tools + custom vtuber tools
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            tools={"type": "preset", "preset": "claude_code"},
            mcp_servers={"vtuber-tools": tools_server},
            allowed_tools=allowed_tools,
            permission_mode="bypassPermissions",
            cli_path=CLI_PATH,
        )
        self.agent = ClaudeSDKClient(options)
        await self.agent.connect()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle a new client connection.

        The client must send a 'register' message first to identify itself.
        Unregistered connections can still send ping/pong but not user messages.
        """
        addr = writer.get_extra_info("peername") or "client"
        provider_id: str | None = None

        # Keep writer reference so we can respond before registration
        self._pending_writers[id(writer)] = writer

        try:
            buffer = ""
            while self.is_running:
                try:
                    data = await reader.read(4096)
                    if not data:
                        break

                    buffer += data.decode("utf-8")

                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():
                            await self._process_message(
                                line, writer, provider_id
                            )
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
        self, line: str, writer: asyncio.StreamWriter, provider_id: str | None
    ):
        """Process a message from a provider."""
        try:
            msg = decode_message(line)
            msg_type = msg.get("type")

            if msg_type == MessageType.REGISTER:
                # Provider registration
                conn = ProviderConnection(
                    provider_type=msg.get("provider", "unknown"),
                    provider_id=msg.get("provider_id", f"anon-{id(writer)}"),
                    writer=writer,
                )
                await self.gateway.register(conn)

            elif msg_type == MessageType.USER_MESSAGE:
                content = msg.get("content", "")
                pid = provider_id or msg.get("provider_id")
                if pid:
                    await self._handle_user_message(content, pid)
                else:
                    logger.warning("User message from unregistered provider, ignoring")

            elif msg_type == MessageType.PING:
                response = encode_message({"type": MessageType.PONG, "is_final": True})
                writer.write(response.encode("utf-8"))
                await writer.drain()

            else:
                logger.warning("Unknown message type: %s", msg_type)

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON message: %s", e)
        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)

    async def _handle_user_message(self, content: str, provider_id: str):
        """Handle a user message by sending it to the agent and routing the response."""
        if not self.agent:
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": "Agent not initialized",
                "is_final": True,
            })
            return

        # Log user message to session
        log_message(self.session_id, "user", content)
        logger.info("[user] %s", _truncate(content))

        try:
            async with self._agent_lock:
                stream_id = f"stream_{provider_id}"
                index = 0
                assistant_text = ""

                await self.agent.query(content)
                async for msg in self.agent.receive_response():
                    _log_stream_event(msg, "agent")

                    tool_name = extract_tool_use_start(msg)
                    if tool_name:
                        await self.gateway.send_to(provider_id, {
                            "type": MessageType.PROGRESS,
                            "tool": tool_name,
                        })
                        continue

                    text = extract_stream_text(msg)
                    if text:
                        assistant_text += text
                        await self.gateway.send_to(provider_id, {
                            "type": MessageType.ASSISTANT_MESSAGE,
                            "stream_id": stream_id,
                            "index": index,
                            "content": text,
                            "is_final": False,
                        })
                        index += 1

                    elif isinstance(msg, ResultMessage):
                        await self.gateway.send_to(provider_id, {
                            "type": MessageType.ASSISTANT_MESSAGE,
                            "stream_id": stream_id,
                            "index": index,
                            "content": "",
                            "is_final": True,
                        })

                if assistant_text.strip():
                    log_message(self.session_id, "assistant", assistant_text.strip())

        except Exception as e:
            logger.error("[agent] error handling message: %s", e, exc_info=True)
            await self.gateway.send_to(provider_id, {
                "type": MessageType.ERROR,
                "content": str(e),
                "is_final": True,
            })

    async def _heartbeat_loop(self):
        """Periodic heartbeat loop."""
        while self.is_running:
            try:
                # Wait for interval
                await asyncio.sleep(self.heartbeat_interval * 60)

                if not self.is_running:
                    break

                # Send heartbeat to agent
                await self._send_heartbeat()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: %s", e, exc_info=True)

    async def _send_heartbeat(self):
        """Send a heartbeat message to the agent."""
        if not self.agent:
            return

        try:
            # Read heartbeat tasks
            heartbeat_path = get_heartbeat_path()
            heartbeat_content = ""

            if heartbeat_path.exists():
                heartbeat_content = heartbeat_path.read_text(encoding="utf-8").strip()

            # Create heartbeat message
            heartbeat_msg = "[HEARTBEAT] "
            if heartbeat_content:
                heartbeat_msg += f"Available tasks:\n{heartbeat_content}\n\n"
            else:
                heartbeat_msg += "No specific tasks defined. "

            heartbeat_msg += "What would you like to do? You can:\n"
            heartbeat_msg += "- Check if there are any scheduled tasks (use schedule_list)\n"
            heartbeat_msg += "- Search past conversations for context (use search_sessions)\n"
            heartbeat_msg += "- Initiate a conversation with the user\n"
            heartbeat_msg += "- Or simply respond with 'HEARTBEAT_OK' if everything is fine."

            # Send heartbeat to agent (acquire lock to prevent concurrent agent access)
            logger.info("[heartbeat] sending heartbeat to agent")
            async with self._agent_lock:
                stream_id = f"heartbeat_{asyncio.get_running_loop().time()}"
                collected_text = ""

                await self.agent.query(heartbeat_msg)
                async for msg in self.agent.receive_response():
                    _log_stream_event(msg, "heartbeat")

                    tool_name = extract_tool_use_start(msg)
                    if tool_name:
                        logger.debug("[heartbeat] using tool: %s", tool_name)

                    text = extract_stream_text(msg)
                    if text:
                        collected_text += text

                    elif isinstance(msg, ResultMessage):
                        pass

            # After collecting all text, decide whether to broadcast
            if collected_text.strip() and "HEARTBEAT_OK" not in collected_text.upper():
                logger.info("[heartbeat] agent responded: %s", _truncate(collected_text))
                await self.gateway.broadcast(
                    {
                        "type": MessageType.HEARTBEAT_MESSAGE,
                        "stream_id": stream_id,
                        "index": 0,
                        "content": collected_text,
                        "is_final": False,
                    }
                )
                await self.gateway.broadcast(
                    {
                        "type": MessageType.HEARTBEAT_MESSAGE,
                        "stream_id": stream_id,
                        "index": 1,
                        "content": "",
                        "is_final": True,
                    }
                )
            else:
                logger.info("[heartbeat] agent replied HEARTBEAT_OK")

            logger.info("[heartbeat] completed")

        except Exception as e:
            logger.error("[heartbeat] error: %s", e, exc_info=True)

    async def shutdown(self):
        """Shutdown the daemon server gracefully."""
        if not self.is_running:
            return  # Already shutting down
        logger.info("Shutting down daemon...")
        self.is_running = False

        # Stop heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Stop task queue consumer
        if self._task_consumer:
            self._task_consumer.cancel()
            try:
                await self._task_consumer
            except asyncio.CancelledError:
                pass

        # Disconnect agent
        if self.agent:
            try:
                await self.agent.disconnect()
            except Exception:
                pass

        # Close all provider connections
        await self.gateway.close_all()

        # Shutdown scheduler
        if self.scheduler:
            try:
                self.scheduler.shutdown()
            except Exception:
                pass

        # Close server
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
            # Keep running until shutdown
            while self.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()


def start_daemon_background():
    """Start the daemon in background mode."""
    import subprocess

    socket_path = get_socket_path()
    pid_path = get_pid_path()

    # Check if daemon is already running
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            # Check if process is running
            os.kill(pid, 0)
            print(f"Daemon is already running (PID: {pid})")
            return
        except (OSError, ProcessLookupError):
            # PID file exists but process not running, clean up
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

    # Start daemon in background using subprocess
    try:
        ensure_config_dir()
        log_path = get_log_path()
        log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115

        subprocess.Popen(
            [sys.executable, "-m", "vtuber.daemon.server"],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # Detach from terminal
        )
        print("Daemon started in background")
        print(f"Log: {log_path}")

        # Wait a moment and verify it started
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

        # Send SIGTERM to daemon
        os.kill(pid, signal.SIGTERM)

        # Wait for process to terminate
        import time

        for _ in range(10):  # Wait up to 10 seconds
            try:
                os.kill(pid, 0)  # Check if process is still running
                time.sleep(1)
            except ProcessLookupError:
                # Process has terminated
                print(f"Daemon stopped (PID: {pid})")
                return

        # If still running, force kill
        print("Daemon did not stop gracefully, forcing...")
        os.kill(pid, signal.SIGKILL)
        print(f"Daemon killed (PID: {pid})")

    except ProcessLookupError:
        print("Daemon is not running (process not found)")
        # Clean up stale PID file
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

        # Check if process is running
        os.kill(pid, 0)

        print(f"Daemon is running (PID: {pid})")
        print(f"Socket: {socket_path}")

        # Try to connect to socket
        if socket_path.exists():
            print("Socket file exists")

            # Try a simple connection test
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
        # Clean up stale PID file
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
