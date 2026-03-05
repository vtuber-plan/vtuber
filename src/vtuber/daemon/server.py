"""Unix Domain Socket server for daemon."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    ResultMessage,
)

from vtuber.daemon.protocol import decode_message, encode_message
from vtuber.daemon.scheduler import TaskScheduler
from vtuber.persona import build_system_prompt
from vtuber.config import (
    ensure_config_dir,
    get_socket_path,
    get_pid_path,
    get_db_path,
    get_persona_path,
    get_user_path,
)
from vtuber.tools.schedule import set_scheduler
from vtuber.tools.memory import log_message, create_session_id
from vtuber.utils import extract_stream_text


def _create_tools_server(include_schedule: bool = True):
    """Create an SDK MCP server with vtuber tools."""
    from vtuber.tools.memory import search_sessions, list_sessions, update_long_term_memory

    tools = [search_sessions, list_sessions, update_long_term_memory]
    allowed = ["search_sessions", "list_sessions", "update_long_term_memory"]

    if include_schedule:
        from vtuber.tools.schedule import schedule_create, schedule_list, schedule_cancel

        tools.extend([schedule_create, schedule_list, schedule_cancel])
        allowed.extend(["schedule_create", "schedule_list", "schedule_cancel"])

    server = create_sdk_mcp_server("vtuber-tools", tools=tools)
    return server, allowed


class DaemonServer:
    """Unix Domain Socket server that manages client connections and agent sessions."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()
        self.is_running = False
        self.clients: list[asyncio.StreamWriter] = []
        self.agent: ClaudeSDKClient | None = None
        self.scheduler: TaskScheduler | None = None
        self.conversation_history: list[dict[str, Any]] = []
        self._server: asyncio.Server | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self.heartbeat_interval: int = 5  # Minutes between heartbeats
        self.session_id: str = create_session_id()

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
        self.scheduler.start()
        set_scheduler(self.scheduler)

        # Setup task execution callback
        self._setup_scheduler_callback()
        print("Scheduler started")

        # Start heartbeat timer
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        print(f"Heartbeat started (every {self.heartbeat_interval} minutes)")

        # Initialize agent
        await self._initialize_agent()
        print("Agent initialized")

        # Start Unix socket server
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        self.is_running = True

        # Write PID file
        pid_path = get_pid_path()
        pid_path.write_text(str(os.getpid()))

        print(f"Daemon started on {self.socket_path}")

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

    def _setup_scheduler_callback(self):
        """Setup callback for scheduled task execution."""
        # Add a listener for job execution
        self.scheduler.scheduler.add_listener(
            self._on_scheduled_task, mask=0x0001  # EVENT_JOB_EXECUTED
        )

    def _on_scheduled_task(self, event):
        """Handle scheduled task execution."""
        if hasattr(event, "job_id"):
            try:
                job = self.scheduler.scheduler.get_job(event.job_id)
                if job and job.kwargs:
                    task_prompt = job.kwargs.get("task", "")
                    if task_prompt:
                        asyncio.create_task(self._execute_scheduled_task(task_prompt))
            except Exception:
                pass

    async def _execute_scheduled_task(self, task_prompt: str):
        """Execute a scheduled task using a temporary subagent."""
        print(f"Executing scheduled task: {task_prompt}")

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
            )
            subagent = ClaudeSDKClient(options)
            await subagent.connect()

            # Execute task
            stream_id = f"task_{id(subagent)}"
            index = 0

            await subagent.query(f"[Scheduled Task] {task_prompt}")
            async for msg in subagent.receive_response():
                text = extract_stream_text(msg)
                if text:
                    # Stream result to all clients
                    response = encode_message(
                        {
                            "type": "task_message",
                            "stream_id": stream_id,
                            "index": index,
                            "content": text,
                            "task": task_prompt,
                            "is_final": False,
                        }
                    )
                    await self._broadcast(response)
                    index += 1

                elif isinstance(msg, ResultMessage):
                    # Send final message
                    response = encode_message(
                        {
                            "type": "task_message",
                            "stream_id": stream_id,
                            "index": index,
                            "content": "",
                            "task": task_prompt,
                            "is_final": True,
                        }
                    )
                    await self._broadcast(response)

            await subagent.disconnect()
            print(f"Task completed: {task_prompt}")

        except Exception as e:
            print(f"Error executing scheduled task: {e}")
            # Notify clients of error
            error_msg = encode_message(
                {
                    "type": "error",
                    "content": f"Error executing task '{task_prompt}': {str(e)}",
                    "is_final": True,
                }
            )
            await self._broadcast(error_msg)

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
        )
        self.agent = ClaudeSDKClient(options)
        await self.agent.connect()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle a new client connection."""
        addr = writer.get_extra_info("peername") or "client"
        print(f"New client connected: {addr}")
        self.clients.append(writer)

        try:
            buffer = ""
            while self.is_running:
                # Read data from client
                try:
                    data = await reader.read(4096)
                    if not data:
                        break

                    buffer += data.decode("utf-8")

                    # Process complete messages (newline-delimited)
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():
                            await self._process_message(line, writer)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"Error reading from client: {e}")
                    break
        finally:
            # Remove client from list
            if writer in self.clients:
                self.clients.remove(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            print(f"Client disconnected: {addr}")

    async def _process_message(
        self, line: str, writer: asyncio.StreamWriter
    ):
        """Process a message from a client."""
        try:
            msg = decode_message(line)
            msg_type = msg.get("type")

            if msg_type == "user_message":
                # User message - send to agent
                content = msg.get("content", "")
                await self._handle_user_message(content, writer)

            elif msg_type == "ping":
                # Ping - respond with pong
                response = encode_message({"type": "pong", "is_final": True})
                writer.write(response.encode("utf-8"))
                await writer.drain()

            else:
                print(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError as e:
            print(f"Invalid JSON message: {e}")
        except Exception as e:
            print(f"Error processing message: {e}")

    async def _handle_user_message(self, content: str, writer: asyncio.StreamWriter):
        """Handle a user message by sending it to the agent and streaming the response."""
        if not self.agent:
            error_msg = encode_message(
                {
                    "type": "error",
                    "content": "Agent not initialized",
                    "is_final": True,
                }
            )
            writer.write(error_msg.encode("utf-8"))
            await writer.drain()
            return

        # Log user message to session
        log_message(self.session_id, "user", content)

        try:
            # Send message to agent
            stream_id = f"stream_{id(writer)}"
            index = 0
            assistant_text = ""

            await self.agent.query(content)
            async for msg in self.agent.receive_response():
                text = extract_stream_text(msg)
                if text:
                    assistant_text += text
                    response = encode_message(
                        {
                            "type": "assistant_message",
                            "stream_id": stream_id,
                            "index": index,
                            "content": text,
                            "is_final": False,
                        }
                    )
                    # Broadcast to all clients
                    await self._broadcast(response)
                    index += 1

                elif isinstance(msg, ResultMessage):
                    # Message complete
                    response = encode_message(
                        {
                            "type": "assistant_message",
                            "stream_id": stream_id,
                            "index": index,
                            "content": "",
                            "is_final": True,
                        }
                    )
                    await self._broadcast(response)

            # Log assistant response to session
            if assistant_text.strip():
                log_message(self.session_id, "assistant", assistant_text.strip())

        except Exception as e:
            print(f"Error handling message: {e}")
            error_msg = encode_message(
                {
                    "type": "error",
                    "content": str(e),
                    "is_final": True,
                }
            )
            writer.write(error_msg.encode("utf-8"))
            await writer.drain()

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
                print(f"Heartbeat error: {e}")

    async def _send_heartbeat(self):
        """Send a heartbeat message to the agent."""
        if not self.agent:
            return

        try:
            # Read heartbeat tasks
            from vtuber.config import get_heartbeat_path

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

            # Send heartbeat to agent
            stream_id = f"heartbeat_{asyncio.get_event_loop().time()}"
            index = 0
            collected_text = ""

            await self.agent.query(heartbeat_msg)
            async for msg in self.agent.receive_response():
                text = extract_stream_text(msg)
                if text:
                    collected_text += text
                    # Only broadcast if it's not just "HEARTBEAT_OK"
                    if "HEARTBEAT_OK" not in collected_text.upper():
                        response = encode_message(
                            {
                                "type": "assistant_message",
                                "stream_id": stream_id,
                                "index": index,
                                "content": text,
                                "is_final": False,
                            }
                        )
                        await self._broadcast(response)
                    index += 1

                elif isinstance(msg, ResultMessage):
                    # Only send final if agent said something non-heartbeat
                    if index > 0 and "HEARTBEAT_OK" not in collected_text.upper():
                        response = encode_message(
                            {
                                "type": "assistant_message",
                                "stream_id": stream_id,
                                "index": index,
                                "content": "",
                                "is_final": True,
                            }
                        )
                        await self._broadcast(response)

            print("Heartbeat sent to agent")

        except Exception as e:
            print(f"Error sending heartbeat: {e}")

    async def _broadcast(self, message: str):
        """Broadcast a message to all connected clients."""
        if not self.clients:
            return

        # Send to all clients
        disconnected = []
        for writer in self.clients:
            try:
                writer.write(message.encode("utf-8"))
                await writer.drain()
            except Exception as e:
                print(f"Error broadcasting to client: {e}")
                disconnected.append(writer)

        # Remove disconnected clients
        for writer in disconnected:
            if writer in self.clients:
                self.clients.remove(writer)

    async def shutdown(self):
        """Shutdown the daemon server gracefully."""
        if not self.is_running:
            return  # Already shutting down
        print("Shutting down daemon...")
        self.is_running = False

        # Stop heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Disconnect agent
        if self.agent:
            try:
                await self.agent.disconnect()
            except Exception:
                pass

        # Close all client connections
        for writer in self.clients:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self.clients.clear()

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

        print("Daemon shutdown complete")

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
        # Use subprocess to start daemon in background
        subprocess.Popen(
            [sys.executable, "-m", "vtuber.daemon.server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # Detach from terminal
        )
        print("Daemon started in background")

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
    try:
        server = DaemonServer()
        asyncio.run(server.run_forever())
    except KeyboardInterrupt:
        print("\nDaemon stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"Daemon error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
