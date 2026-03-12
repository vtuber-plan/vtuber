"""CLI provider - terminal-based chat with rich UI.

Architecture:
- A background `_consume_queue` task continuously drains the message queue.
- All messages render immediately above the prompt via patch_stdout.
- The prompt stays at the bottom of the terminal at all times.
- Status is shown in the bottom toolbar (thinking, tool use).
"""

import asyncio
import sys
from io import StringIO

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from vtuber.config import ensure_config_dir
from vtuber.providers.base import QueuedProvider

# stderr console for banners rendered outside the prompt loop
console = Console(stderr=True)

# stdout-based console for rendering Rich objects to a string,
# which then gets written through patch_stdout's proxy
_render_buf = StringIO()
_render_console = Console(file=_render_buf, force_terminal=True)


def _print_above_prompt(renderable) -> None:
    """Render a Rich object and write it to stdout (above the prompt via patch_stdout)."""
    _render_buf.truncate(0)
    _render_buf.seek(0)
    _render_console.print(renderable)
    text = _render_buf.getvalue()
    sys.stdout.write(text)
    sys.stdout.flush()


def _render_agent(content: str) -> None:
    _print_above_prompt(Panel(
        Markdown(content.strip()),
        title="[bold cyan]Agent[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))


def _render_task(content: str) -> None:
    _print_above_prompt(Panel(
        Markdown(content.strip()),
        title="[bold yellow]Task[/bold yellow]",
        border_style="yellow",
        padding=(1, 2),
    ))


def _render_heartbeat(content: str) -> None:
    _print_above_prompt(Panel(
        Markdown(content.strip()),
        title="[bold cyan]Heartbeat[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))


# ── CLI Provider ──────────────────────────────────────────────────


class CLIProvider(QueuedProvider):
    """Terminal-based provider using prompt_toolkit + rich.

    The prompt stays at the bottom at all times. Messages render above it
    in real time via patch_stdout.
    """

    provider_type = "cli"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # CLI owner always gets a stable provider_id so the session_id
        # (dm:cli:owner) is deterministic across restarts.
        self.provider_id = "cli"

        history_path = ensure_config_dir() / "cli_history"
        self.session = PromptSession(
            history=FileHistory(str(history_path)),
            bottom_toolbar=self._get_toolbar,
            style=Style.from_dict({
                "bottom-toolbar": "bg:#000000 noreverse",
            }),
        )

        # Status for toolbar
        self._status: str = ""
        self._spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._spinner_idx = 0
        self._spinner_task: asyncio.Task | None = None

        # Task buffering (tasks arrive in chunks, render when done)
        self._tasks: dict[str, str] = {}

        # Background consumer
        self._consumer_task: asyncio.Task | None = None

    # ── Toolbar ──────────────────────────────────────────────────

    def _get_toolbar(self):
        if self._status:
            frame = self._spinner_frames[self._spinner_idx % len(self._spinner_frames)]
            return HTML(
                f'<style fg="ansicyan" bg="ansiblack"> {frame} {self._status}</style>'
            )
        return HTML(
            '<style fg="ansicyan" bg="ansiblack"> Enter 发送</style>'
        )

    def _invalidate(self) -> None:
        """Refresh the prompt UI (e.g., to update toolbar)."""
        app = self.session.app
        if app is not None:
            app.invalidate()

    async def _animate_spinner(self) -> None:
        """Tick the spinner frame and refresh toolbar periodically."""
        try:
            while True:
                await asyncio.sleep(0.08)
                if self._status:
                    self._spinner_idx += 1
                    self._invalidate()
        except asyncio.CancelledError:
            pass

    def _set_status(self, text: str) -> None:
        """Update status and start/stop spinner animation as needed."""
        was_active = bool(self._status)
        self._status = text
        self._invalidate()

        if text and not was_active:
            # Start spinner animation
            self._spinner_idx = 0
            self._spinner_task = asyncio.ensure_future(self._animate_spinner())
        elif not text and was_active and self._spinner_task:
            # Stop spinner animation
            self._spinner_task.cancel()
            self._spinner_task = None

    # ── Callback overrides ────────────────────────────────────────

    async def on_task(self, content: str, task: str, *, done: bool) -> None:
        await self._msg_queue.put({
            "type": "task_message",
            "content": content,
            "task": task,
            "done": done,
        })

    async def on_heartbeat(self, content: str) -> None:
        await self._msg_queue.put({
            "type": "heartbeat_message",
            "content": content,
        })

    async def on_disconnected(self) -> None:
        _print_above_prompt("[yellow]Daemon 连接已关闭[/yellow]")

    # ── Background consumer ───────────────────────────────────────

    async def _consume_queue(self) -> None:
        """Continuously consume and render messages from the queue."""
        while self.running:
            try:
                msg = await asyncio.wait_for(self._msg_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            msg_type = msg.get("type")
            content = msg.get("content", "")

            if msg_type == "assistant_message":
                if content.strip():
                    _render_agent(content)
                if msg.get("done"):
                    self._set_status("")

            elif msg_type == "progress":
                tool = msg.get("tool", "")
                self._set_status(f"⚙ {tool}" if tool else "思考中...")

            elif msg_type == "task_message":
                task_name = msg.get("task", "task")
                if content:
                    self._tasks.setdefault(task_name, "")
                    self._tasks[task_name] += content
                if msg.get("done") and self._tasks.get(task_name, "").strip():
                    _render_task(self._tasks.pop(task_name))

            elif msg_type == "heartbeat_message":
                if content.strip():
                    _render_heartbeat(content)

            elif msg_type == "error":
                if content:
                    _print_above_prompt(f"[bold red]{content}[/bold red]")
                self._set_status("")

    # ── Main loop ─────────────────────────────────────────────────

    async def run(self) -> None:
        """Run the interactive CLI."""
        if not await self.connect():
            console.print(Panel(
                "[red]Daemon 未运行[/red]\n\n"
                "请先启动 daemon：[bold]vtuber start[/bold]",
                title="连接失败",
                border_style="red",
            ))
            return

        console.print()
        console.print(Panel(
            "[green]已连接到 VTuber[/green]\n"
            "输入消息并回车发送，输入 [bold]/quit[/bold] 退出",
            title="VTuber Chat",
            border_style="green",
        ))
        console.print()

        # Start background consumer
        self._consumer_task = asyncio.create_task(self._consume_queue())

        try:
            with patch_stdout(raw=True):
                while self.running:
                    try:
                        user_input = await self.session.prompt_async(
                            HTML(
                                "<ansigreen><b>You</b></ansigreen>"
                                "<ansigray> › </ansigray>"
                            ),
                        )

                        if user_input.strip().lower() in ("/quit", "/exit"):
                            break

                        if not user_input.strip():
                            continue

                        self._set_status("思考中...")
                        await self.send_message(user_input)

                    except EOFError:
                        break
                    except KeyboardInterrupt:
                        continue

        finally:
            self._set_status("")
            if self._consumer_task:
                self._consumer_task.cancel()
                try:
                    await self._consumer_task
                except asyncio.CancelledError:
                    pass
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
