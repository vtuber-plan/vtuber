"""CLI client for connecting to the VTuber daemon with rich UI."""

import asyncio
import os
import sys
from io import StringIO
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from vtuber.daemon.protocol import encode_message, decode_message
from vtuber.config import get_socket_path, ensure_config_dir

console = Console()

SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

CHAT_STYLE = Style.from_dict({
    "bottom-toolbar": "bg:#1a1a2e",
    "spinner": "#00aaff bold",
    "spinner-text": "#aaaaaa",
    "toolbar-sep": "#444444",
    "toolbar-hint": "#666666",
})


class CLIClient:
    """Command-line client for interacting with the VTuber daemon.

    Uses patch_stdout to keep the input prompt fixed at the bottom of the
    terminal (like Discord) while messages scroll above it.
    """

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.running = False
        self._stream_buffer = ""
        self._is_streaming = False
        self._spinner_frame = 0
        self._spinner_task: asyncio.Task | None = None

        # Setup prompt with history
        history_path = ensure_config_dir() / "cli_history"
        self.session = PromptSession(history=FileHistory(str(history_path)))

    # ── Toolbar & Rendering ──────────────────────────────────────────

    def _get_toolbar(self):
        """Bottom toolbar: spinner during streaming, hints otherwise."""
        if self._is_streaming:
            frame = SPINNER_CHARS[self._spinner_frame % len(SPINNER_CHARS)]
            return [
                ("class:spinner", f" {frame} "),
                ("class:spinner-text", "思考中... "),
                ("class:toolbar-sep", "│ "),
                ("class:toolbar-hint", "/quit 退出 "),
            ]
        return [("class:toolbar-hint", " /quit 退出 ")]

    def _render_panel(self, label: str, text: str, style: str = "cyan") -> str:
        """Render a rich Panel + Markdown to an ANSI string."""
        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 80
        sio = StringIO()
        c = Console(file=sio, force_terminal=True, width=width)
        c.print()
        c.print(
            Panel(
                Markdown(text),
                title=f"[bold {style}]{label}[/bold {style}]",
                border_style=style,
                padding=(1, 2),
            )
        )
        return sio.getvalue()

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
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        console.print("\n[dim]已断开连接[/dim]")

    # ── Messaging ────────────────────────────────────────────────────

    async def send_message(self, content: str):
        """Send a user message to the daemon."""
        if not self.writer:
            return
        try:
            msg = encode_message({"type": "user_message", "content": content})
            self.writer.write(msg.encode("utf-8"))
            await self.writer.drain()
        except Exception as e:
            print(f"\033[31m发送失败：{e}\033[0m", flush=True)

    async def receive_messages(self):
        """Receive and display messages from the daemon."""
        if not self.reader:
            return

        buffer = ""
        try:
            while self.running:
                data = await self.reader.read(4096)
                if not data:
                    print("\n\033[33mDaemon 连接已关闭\033[0m", flush=True)
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
                print(f"\n\033[31m接收消息错误：{e}\033[0m", flush=True)

    async def _handle_message(self, line: str):
        """Handle a message from the daemon.

        Uses print() instead of console.print() so that output goes through
        the patch_stdout proxy and appears above the fixed input prompt.
        """
        try:
            msg = decode_message(line)
            msg_type = msg.get("type")

            if msg_type in ("assistant_message", "task_message"):
                content = msg.get("content", "")
                is_final = msg.get("is_final", False)

                if content:
                    self._stream_buffer += content
                    if not self._is_streaming:
                        self._is_streaming = True

                if is_final:
                    # Stop spinner first
                    self._is_streaming = False
                    if self._spinner_task:
                        self._spinner_task.cancel()
                        self._spinner_task = None

                    # Render complete response as markdown panel
                    if self._stream_buffer.strip():
                        label = "AI" if msg_type == "assistant_message" else "Task"
                        output = self._render_panel(label, self._stream_buffer.strip())
                        print(output, flush=True)

                    self._stream_buffer = ""

                    # Refresh toolbar to remove spinner
                    if self.session.app:
                        self.session.app.invalidate()

            elif msg_type == "error":
                error = msg.get("content", "Unknown error")
                print(f"\n\033[1;31m错误：\033[0m {error}", flush=True)

            elif msg_type == "pong":
                pass

        except Exception as e:
            print(f"\n\033[31m处理消息错误：{e}\033[0m", flush=True)

    # ── Spinner ──────────────────────────────────────────────────────

    async def _animate_spinner(self):
        """Animate the spinner in the bottom toolbar."""
        try:
            while True:
                self._spinner_frame += 1
                if self.session.app:
                    self.session.app.invalidate()
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            pass

    # ── Main Loop ────────────────────────────────────────────────────

    async def run(self):
        """Run the interactive CLI client.

        Uses patch_stdout to keep the input prompt fixed at the bottom of the
        terminal. All output (AI responses, errors) appears above the prompt,
        giving a Discord-like chat experience.
        """
        if not await self.connect():
            return

        receive_task = asyncio.create_task(self.receive_messages())

        try:
            with patch_stdout():
                while self.running:
                    try:
                        user_input = await self.session.prompt_async(
                            HTML(
                                "<ansigreen><b> You </b></ansigreen>"
                                "<ansigray> › </ansigray>"
                            ),
                            bottom_toolbar=self._get_toolbar,
                            style=CHAT_STYLE,
                        )

                        if user_input.strip().lower() in ("/quit", "/exit"):
                            break

                        if user_input.strip():
                            # Show user message as a panel above the prompt
                            user_panel = self._render_panel(
                                "You", user_input.strip(), style="green"
                            )
                            print(user_panel, flush=True)

                            # Start spinner and send message
                            self._is_streaming = True
                            self._spinner_task = asyncio.create_task(
                                self._animate_spinner()
                            )
                            await self.send_message(user_input)

                    except EOFError:
                        break
                    except KeyboardInterrupt:
                        continue

        finally:
            if self._spinner_task:
                self._spinner_task.cancel()
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
