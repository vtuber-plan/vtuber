"""CLI client for connecting to the VTuber daemon with rich UI."""

import asyncio
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from vtuber.daemon.protocol import encode_message, decode_message
from vtuber.config import get_socket_path, ensure_config_dir

console = Console()


class CLIClient:
    """Command-line client for interacting with the VTuber daemon.

    Linear flow: input → spinner → response panel → next input.
    """

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.running = False
        self._msg_queue: asyncio.Queue = asyncio.Queue()
        self._pending_heartbeats: list[str] = []
        self._reader_task: asyncio.Task | None = None

        # Setup prompt with history
        history_path = ensure_config_dir() / "cli_history"
        self.session = PromptSession(history=FileHistory(str(history_path)))

    # ── Connection ───────────────────────────────────────────────────

    async def connect(self):
        """Connect to the daemon server."""
        if not self.socket_path.exists():
            console.print(
                Panel(
                    "[red]Daemon 未运行[/red]\n\n"
                    "请先启动 daemon：[bold]vtuber start[/bold]",
                    title="连接失败",
                    border_style="red",
                )
            )
            return False

        try:
            self.reader, self.writer = await asyncio.open_unix_connection(
                str(self.socket_path)
            )
            self.running = True

            # Start background socket reader
            self._reader_task = asyncio.create_task(self._read_socket())

            console.print()
            console.print(
                Panel(
                    "[green]已连接到 VTuber daemon[/green]\n"
                    "输入消息并回车发送，输入 [bold]/quit[/bold] 退出",
                    title="VTuber Chat",
                    border_style="green",
                )
            )
            console.print()
            return True
        except Exception as e:
            console.print(f"[red]连接失败：{e}[/red]")
            return False

    async def disconnect(self):
        """Disconnect from the daemon server."""
        self.running = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        console.print("\n[dim]已断开连接[/dim]")

    # ── Socket Reader ─────────────────────────────────────────────────

    async def _read_socket(self):
        """Background task: read from socket and enqueue decoded messages."""
        if not self.reader:
            return
        buffer = ""
        try:
            while self.running:
                data = await self.reader.read(4096)
                if not data:
                    console.print("\n[yellow]Daemon 连接已关闭[/yellow]")
                    self.running = False
                    break
                buffer += data.decode("utf-8")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        try:
                            msg = decode_message(line)
                            await self._msg_queue.put(msg)
                        except Exception:
                            pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.running:
                console.print(f"\n[red]接收消息错误：{e}[/red]")

    # ── Messaging ─────────────────────────────────────────────────────

    async def send_message(self, content: str):
        """Send a user message to the daemon."""
        if not self.writer:
            return
        try:
            msg = encode_message({"type": "user_message", "content": content})
            self.writer.write(msg.encode("utf-8"))
            await self.writer.drain()
        except Exception as e:
            console.print(f"[red]发送失败：{e}[/red]")

    async def _drain_pending(self):
        """Display any unsolicited messages queued while user was typing."""
        # Show heartbeat messages that arrived during _wait_for_response
        for hb in self._pending_heartbeats:
            console.print(
                Panel(
                    Markdown(hb.strip()),
                    title="[bold cyan]Agent[/bold cyan]",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )
        self._pending_heartbeats.clear()

        while not self._msg_queue.empty():
            try:
                msg = self._msg_queue.get_nowait()
                msg_type = msg.get("type")
                content = msg.get("content", "")
                if msg_type in ("assistant_message", "task_message", "heartbeat_message"):
                    is_final = msg.get("is_final", False)
                    if content and is_final:
                        labels = {
                            "assistant_message": "Agent",
                            "task_message": "Task",
                            "heartbeat_message": "Agent",
                        }
                        label = labels.get(msg_type, "Agent")
                        console.print(
                            Panel(
                                Markdown(content.strip()),
                                title=f"[bold cyan]{label}[/bold cyan]",
                                border_style="cyan",
                                padding=(1, 2),
                            )
                        )
                elif msg_type == "error":
                    console.print(
                        f"[bold red]错误：[/bold red]{content}"
                    )
            except asyncio.QueueEmpty:
                break

    async def _wait_for_response(self):
        """Show spinner while collecting streamed response, then render panel."""
        collected = ""
        label = "Agent"

        spinner = Spinner("dots", text=Text(" 思考中...", style="dim"))

        with Live(spinner, console=console, transient=True, refresh_per_second=12) as live:
            while True:
                try:
                    msg = await asyncio.wait_for(self._msg_queue.get(), timeout=300)
                except asyncio.TimeoutError:
                    console.print("[yellow]响应超时（5分钟无活动）[/yellow]")
                    return

                msg_type = msg.get("type")

                if msg_type == "progress":
                    # Agent is using a tool — update spinner, reset timeout
                    tool = msg.get("tool", "")
                    live.update(
                        Spinner("dots", text=Text(f" 使用工具: {tool}", style="dim"))
                    )
                    continue

                if msg_type in ("assistant_message", "task_message"):
                    content = msg.get("content", "")
                    is_final = msg.get("is_final", False)

                    if content:
                        collected += content

                    if msg_type == "task_message":
                        label = "Task"

                    if is_final:
                        break

                elif msg_type == "heartbeat_message":
                    # Heartbeat arrived while waiting — queue for drain later
                    content = msg.get("content", "")
                    if content:
                        self._pending_heartbeats.append(content)
                    continue

                elif msg_type == "error":
                    console.print(
                        f"\n[bold red]错误：[/bold red]{msg.get('content', '')}"
                    )
                    return

                elif msg_type == "pong":
                    continue

        # Spinner is gone (transient=True), now render the full response
        if collected.strip():
            console.print(
                Panel(
                    Markdown(collected.strip()),
                    title=f"[bold cyan]{label}[/bold cyan]",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )

    # ── Main Loop ─────────────────────────────────────────────────────

    async def run(self):
        """Run the interactive CLI client.

        Linear flow: prompt → send → spinner → response → prompt.
        """
        if not await self.connect():
            return

        try:
            while self.running:
                # Show any messages that arrived while waiting for input
                await self._drain_pending()

                try:
                    user_input = await self.session.prompt_async(
                        HTML(
                            "<ansigreen><b>You</b></ansigreen>"
                            "<ansigray> › </ansigray>"
                        ),
                    )

                    if user_input.strip().lower() in ("/quit", "/exit"):
                        break

                    if user_input.strip():
                        await self.send_message(user_input)
                        await self._wait_for_response()
                        console.print()  # blank line before next prompt

                except EOFError:
                    break
                except KeyboardInterrupt:
                    continue

        finally:
            await self.disconnect()


def main():
    """Main entry point for CLI client."""
    try:
        client = CLIClient()
        asyncio.run(client.run())
    except KeyboardInterrupt:
        console.print("\n[dim]再见！[/dim]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]客户端错误：{e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
