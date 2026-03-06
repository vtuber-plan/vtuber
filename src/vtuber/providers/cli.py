"""CLI provider - terminal-based chat with rich UI."""

import asyncio
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from vtuber.config import ensure_config_dir, get_config
from vtuber.providers.base import Provider

console = Console()


class CLIProvider(Provider):
    """Terminal-based provider using prompt_toolkit + rich.

    Linear flow: input → spinner → response panel → next input.
    """

    provider_type = "cli"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._msg_queue: asyncio.Queue = asyncio.Queue()
        self._pending_heartbeats: list[str] = []

        # Setup prompt with history
        history_path = ensure_config_dir() / "cli_history"
        self.session = PromptSession(history=FileHistory(str(history_path)))

    # ── Provider callbacks ───────────────────────────────────────

    async def on_response(self, content: str, is_final: bool) -> None:
        await self._msg_queue.put({
            "type": "assistant_message",
            "content": content,
            "is_final": is_final,
        })

    async def on_progress(self, tool: str) -> None:
        await self._msg_queue.put({"type": "progress", "tool": tool})

    async def on_error(self, error: str) -> None:
        await self._msg_queue.put({"type": "error", "content": error})

    async def on_heartbeat(self, content: str, is_final: bool) -> None:
        await self._msg_queue.put({
            "type": "heartbeat_message",
            "content": content,
            "is_final": is_final,
        })

    async def on_task(self, content: str, task: str, is_final: bool) -> None:
        await self._msg_queue.put({
            "type": "task_message",
            "content": content,
            "task": task,
            "is_final": is_final,
        })

    async def on_disconnected(self) -> None:
        console.print("\n[yellow]Daemon 连接已关闭[/yellow]")

    # ── UI helpers ───────────────────────────────────────────────

    async def _drain_pending(self):
        """Display any unsolicited messages queued while user was typing."""
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

        # Accumulate streamed chunks by message type
        collected: dict[str, str] = {}

        while not self._msg_queue.empty():
            try:
                msg = self._msg_queue.get_nowait()
                msg_type = msg.get("type")
                content = msg.get("content", "")

                if msg_type in ("assistant_message", "task_message", "heartbeat_message"):
                    is_final = msg.get("is_final", False)
                    if content:
                        collected[msg_type] = collected.get(msg_type, "") + content
                    if is_final:
                        text = collected.pop(msg_type, "")
                        if text.strip():
                            labels = {
                                "assistant_message": "Agent",
                                "task_message": "Task",
                                "heartbeat_message": "Agent",
                            }
                            label = labels.get(msg_type, "Agent")
                            console.print(
                                Panel(
                                    Markdown(text.strip()),
                                    title=f"[bold cyan]{label}[/bold cyan]",
                                    border_style="cyan",
                                    padding=(1, 2),
                                )
                            )
                elif msg_type == "error":
                    console.print(f"[bold red]{content}[/bold red]")
            except asyncio.QueueEmpty:
                break

        # Show any incomplete streams (no final message received yet)
        labels = {
            "assistant_message": "Agent",
            "task_message": "Task",
            "heartbeat_message": "Agent",
        }
        for msg_type, text in collected.items():
            if text.strip():
                label = labels.get(msg_type, "Agent")
                console.print(
                    Panel(
                        Markdown(text.strip()),
                        title=f"[bold cyan]{label}[/bold cyan]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )

    async def _wait_for_response(self):
        """Show spinner while collecting streamed response, then render panel."""
        collected = ""
        label = "Agent"

        spinner = Spinner("dots", text=Text(" 思考中...", style="dim"))

        with Live(spinner, console=console, transient=True, refresh_per_second=12) as live:
            while True:
                try:
                    msg = await asyncio.wait_for(
                        self._msg_queue.get(), timeout=get_config().response_timeout
                    )
                except asyncio.TimeoutError:
                    console.print("[yellow]响应超时（5分钟无活动）[/yellow]")
                    return

                msg_type = msg.get("type")

                if msg_type == "progress":
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
                    content = msg.get("content", "")
                    if content:
                        self._pending_heartbeats.append(content)
                    continue

                elif msg_type == "error":
                    console.print(
                        f"\n[bold red]{msg.get('content', '')}[/bold red]"
                    )
                    return

                elif msg_type == "pong":
                    continue

        if collected.strip():
            console.print(
                Panel(
                    Markdown(collected.strip()),
                    title=f"[bold cyan]{label}[/bold cyan]",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )

    # ── Main loop ────────────────────────────────────────────────

    async def run(self) -> None:
        """Run the interactive CLI provider.

        Linear flow: prompt → send → spinner → response → prompt.
        """
        if not await self.connect():
            console.print(
                Panel(
                    "[red]Daemon 未运行[/red]\n\n"
                    "请先启动 daemon：[bold]vtuber start[/bold]",
                    title="连接失败",
                    border_style="red",
                )
            )
            return

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

        try:
            while self.running:
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
            console.print("\n[dim]已断开连接[/dim]")


def main():
    """Main entry point for CLI provider."""
    try:
        provider = CLIProvider()
        asyncio.run(provider.run())
    except KeyboardInterrupt:
        console.print("\n[dim]再见！[/dim]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]客户端错误：{e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
