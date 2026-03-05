"""CLI client for connecting to the VTuber daemon with rich UI."""

import asyncio
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.text import Text

from vtuber.daemon.protocol import encode_message, decode_message
from vtuber.config import get_socket_path, ensure_config_dir

console = Console()


class CLIClient:
    """Command-line client for interacting with the VTuber daemon."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.running = False
        self._stream_buffer = ""
        self._is_streaming = False
        self._live: Live | None = None

        # Setup prompt with history
        history_path = ensure_config_dir() / "cli_history"
        self.session = PromptSession(history=FileHistory(str(history_path)))

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
            console.print(
                Panel(
                    "[green]已连接到 VTuber daemon[/green]\n"
                    "输入消息并回车发送，输入 [bold]/quit[/bold] 或 [bold]/exit[/bold] 退出",
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
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        console.print("\n[dim]已断开连接[/dim]")

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

    async def receive_messages(self):
        """Receive and display messages from the daemon."""
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
                        await self._handle_message(line)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.running:
                console.print(f"\n[red]接收消息错误：{e}[/red]")

    async def _handle_message(self, line: str):
        """Handle a message from the daemon."""
        try:
            msg = decode_message(line)
            msg_type = msg.get("type")

            if msg_type in ("assistant_message", "task_message"):
                content = msg.get("content", "")
                is_final = msg.get("is_final", False)

                if content:
                    self._stream_buffer += content
                    # Show streaming indicator
                    if not self._is_streaming:
                        self._is_streaming = True

                if is_final:
                    # Render complete response as markdown
                    if self._stream_buffer.strip():
                        label = "AI" if msg_type == "assistant_message" else "Task"
                        console.print()
                        console.print(
                            Panel(
                                Markdown(self._stream_buffer.strip()),
                                title=f"[bold cyan]{label}[/bold cyan]",
                                border_style="cyan",
                                padding=(1, 2),
                            )
                        )
                        console.print()
                    self._stream_buffer = ""
                    self._is_streaming = False

            elif msg_type == "error":
                error = msg.get("content", "Unknown error")
                console.print(f"\n[red bold]错误：[/red bold] {error}")

            elif msg_type == "pong":
                pass

        except Exception as e:
            console.print(f"\n[red]处理消息错误：{e}[/red]")

    async def run(self):
        """Run the interactive CLI client."""
        if not await self.connect():
            return

        receive_task = asyncio.create_task(self.receive_messages())

        try:
            while self.running:
                try:
                    loop = asyncio.get_event_loop()
                    user_input = await loop.run_in_executor(
                        None,
                        lambda: self.session.prompt(
                            HTML("<ansigreen><b>You</b></ansigreen> <ansigray>›</ansigray> ")
                        ),
                    )

                    if user_input.strip().lower() in ["/quit", "/exit"]:
                        break

                    if user_input.strip():
                        await self.send_message(user_input)
                        # Show thinking spinner
                        console.print("[dim]思考中...[/dim]", end="\r")

                except EOFError:
                    break
                except KeyboardInterrupt:
                    console.print()
                    continue

        finally:
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
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
