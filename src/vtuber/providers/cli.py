"""CLI provider - terminal-based chat with rich UI."""

import asyncio
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from vtuber.config import ensure_config_dir, get_config
from vtuber.providers.base import QueuedProvider

console = Console()


class CLIProvider(QueuedProvider):
    """Terminal-based provider using prompt_toolkit + rich.

    Each agent response is collected fully, then rendered as a
    Rich Markdown panel.
    """

    provider_type = "cli"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._prompting = False
        self._task_buffer = ""

        history_path = ensure_config_dir() / "cli_history"
        self.session = PromptSession(history=FileHistory(str(history_path)))

    # ── Callback overrides for INPUT phase ────────────────────────

    async def on_task(self, content: str, task: str, *, done: bool) -> None:
        if self._prompting:
            if content:
                self._task_buffer += content
            if done:
                if self._task_buffer.strip():
                    console.print()
                    console.print(Panel(
                        Markdown(self._task_buffer.strip()),
                        title="[bold cyan]Task[/bold cyan]",
                        border_style="cyan",
                        padding=(1, 2),
                    ))
                self._task_buffer = ""
        else:
            await self._msg_queue.put({
                "type": "task_message",
                "content": content,
                "task": task,
                "done": done,
            })

    async def on_heartbeat(self, content: str) -> None:
        if self._prompting:
            if content.strip():
                console.print()
                console.print(Panel(
                    Markdown(content.strip()),
                    title="[bold cyan]Agent[/bold cyan]",
                    border_style="cyan",
                    padding=(1, 2),
                ))
        else:
            await self._msg_queue.put({
                "type": "heartbeat_message",
                "content": content,
            })

    async def on_disconnected(self) -> None:
        console.print("\n[yellow]Daemon 连接已关闭[/yellow]")

    # ── Response streaming ────────────────────────────────────────

    async def _drain_pending(self) -> None:
        """Display any messages queued between prompts."""
        labels = {
            "assistant_message": "Agent",
            "task_message": "Task",
            "heartbeat_message": "Agent",
        }
        while not self._msg_queue.empty():
            try:
                msg = self._msg_queue.get_nowait()
                msg_type = msg.get("type")
                content = msg.get("content", "")

                if msg_type in labels and content.strip():
                    console.print(
                        Panel(
                            Markdown(content.strip()),
                            title=f"[bold cyan]{labels[msg_type]}[/bold cyan]",
                            border_style="cyan",
                            padding=(1, 2),
                        )
                    )
                elif msg_type == "error":
                    console.print(f"[bold red]{content}[/bold red]")
            except asyncio.QueueEmpty:
                break

    async def _wait_for_response(self) -> None:
        """Wait for agent response and render each message as its own Panel."""
        task_bufs: dict[str, str] = {}
        spinner: Live | None = None

        def start_spinner(text: str = " 思考中..."):
            nonlocal spinner
            spinner = Live(
                Spinner("dots", text=Text(text, style="dim")),
                console=console, transient=True, refresh_per_second=12,
            )
            spinner.start()

        def stop_spinner():
            nonlocal spinner
            if spinner:
                spinner.stop()
                spinner = None

        start_spinner()

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(
                        self._msg_queue.get(),
                        timeout=get_config().response_timeout,
                    )
                except asyncio.TimeoutError:
                    console.print("[yellow]响应超时[/yellow]")
                    return

                msg_type = msg.get("type")

                if msg_type == "assistant_message":
                    content = msg.get("content", "")
                    if content.strip():
                        stop_spinner()
                        console.print(Panel(
                            Markdown(content.strip()),
                            title="[bold cyan]Agent[/bold cyan]",
                            border_style="cyan",
                            padding=(1, 2),
                        ))
                    if msg.get("done"):
                        break
                    start_spinner()

                elif msg_type == "progress":
                    stop_spinner()
                    console.print(Text(f"  ⚙ {msg.get('tool', '')}", style="dim"))
                    start_spinner(f" ⚙ {msg.get('tool', '')}")

                elif msg_type == "task_message":
                    content = msg.get("content", "")
                    task_name = msg.get("task", "task")
                    sid = msg.get("stream_id", task_name)
                    if content:
                        task_bufs.setdefault(sid, "")
                        task_bufs[sid] += content
                    if msg.get("done") and task_bufs.get(sid, "").strip():
                        stop_spinner()
                        console.print(Panel(
                            Markdown(task_bufs.pop(sid).strip()),
                            title="[bold yellow]Task[/bold yellow]",
                            border_style="yellow",
                            padding=(1, 2),
                        ))
                        start_spinner()

                elif msg_type == "heartbeat_message":
                    content = msg.get("content", "")
                    if content.strip():
                        stop_spinner()
                        console.print(Panel(
                            Markdown(content.strip()),
                            title="[bold cyan]Agent[/bold cyan]",
                            border_style="cyan",
                            padding=(1, 2),
                        ))
                        start_spinner()

                elif msg_type == "error":
                    console.print(f"[bold red]{msg.get('content', '')}[/bold red]")
                    return

                elif msg_type == "pong":
                    continue

        finally:
            stop_spinner()

    # ── Main loop ────────────────────────────────────────────────

    async def run(self) -> None:
        """Run the interactive CLI provider."""
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
                    self._prompting = True
                    with patch_stdout():
                        user_input = await self.session.prompt_async(
                            HTML(
                                "<ansigreen><b>You</b></ansigreen>"
                                "<ansigray> › </ansigray>"
                            ),
                        )
                    self._prompting = False

                    if user_input.strip().lower() in ("/quit", "/exit"):
                        break

                    if user_input.strip():
                        await self.send_message(user_input)
                        await self._wait_for_response()
                        console.print()  # blank line before next prompt

                except EOFError:
                    break
                except KeyboardInterrupt:
                    self._prompting = False
                    continue

        finally:
            self._prompting = False
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
