"""Mock group chat provider for testing group agent functionality."""

import asyncio
import random
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text

from vtuber.config import get_config
from vtuber.providers.base import ChatMessage, QueuedProvider

console = Console()

CHANNEL_ID = "mock-group-001"

FAKE_USERS = ["Alice", "Bob", "Charlie"]

FAKE_CONVERSATIONS: list[list[ChatMessage]] = [
    [
        ChatMessage(sender="Alice", content="今天天气真好啊"),
        ChatMessage(sender="Bob", content="是啊，想出去走走"),
        ChatMessage(sender="Alice", content="要不要一起去公园？"),
    ],
    [
        ChatMessage(sender="Bob", content="有人看了昨晚的比赛吗？"),
        ChatMessage(sender="Charlie", content="看了！最后那个进球太精彩了"),
        ChatMessage(sender="Bob", content="对啊，简直不敢相信"),
    ],
    [
        ChatMessage(sender="Charlie", content="周末有什么计划？"),
        ChatMessage(sender="Alice", content="想宅在家看电影"),
        ChatMessage(sender="Charlie", content="推荐几部？"),
    ],
    [
        ChatMessage(sender="Alice", content="今天的午饭吃什么好呢"),
        ChatMessage(sender="Bob", content="楼下新开了一家拉面店"),
        ChatMessage(sender="Charlie", content="听说不错，评分挺高的"),
    ],
]

SENDER_COLORS = {
    "Alice": "magenta",
    "Bob": "blue",
    "Charlie": "yellow",
}

class MockGroupProvider(QueuedProvider):
    """Mock group chat provider for testing.

    Simulates a group chat with pre-seeded fake messages.
    Messages are sent to the daemon individually (as in real group chat).
    Use /flush to manually trigger agent evaluation, or /mention to
    simulate a mention that triggers immediate reply.
    """

    provider_type = "mock-group"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._round = 0
        self._msg_count = 0
        self.session = PromptSession()

    # ── Provider callback overrides ───────────────────────────────

    async def on_heartbeat(self, content: str) -> None:
        pass  # ignore heartbeats in mock

    async def on_task(self, content: str, task: str, *, done: bool) -> None:
        pass  # ignore tasks in mock

    async def on_disconnected(self) -> None:
        console.print("\n[yellow]Daemon 连接已关闭[/yellow]")

    # ── Display helpers ──────────────────────────────────────────

    def _print_chat_message(self, msg: ChatMessage):
        """Print a single chat message in group style."""
        color = SENDER_COLORS.get(msg.sender, "green")
        console.print(
            f"  [{color} bold]{msg.sender}[/{color} bold]"
            f"[dim]:[/dim] {msg.content}"
        )

    def _get_seed_messages(self) -> list[ChatMessage]:
        """Get pre-seeded messages for the current round."""
        idx = self._round % len(FAKE_CONVERSATIONS)
        return list(FAKE_CONVERSATIONS[idx])

    async def _wait_for_response(self) -> str | None:
        """Wait for agent response, return content or None if no-response."""
        collected = ""

        spinner = Spinner("dots", text=Text(" Agent 思考中...", style="dim"))

        with Live(spinner, console=console, transient=True, refresh_per_second=12) as live:
            while True:
                try:
                    msg = await asyncio.wait_for(
                        self._msg_queue.get(), timeout=get_config().response_timeout
                    )
                except asyncio.TimeoutError:
                    console.print("[yellow]响应超时[/yellow]")
                    return None

                msg_type = msg.get("type")

                if msg_type == "progress":
                    tool = msg.get("tool", "")
                    live.update(
                        Spinner("dots", text=Text(f" 使用工具: {tool}", style="dim"))
                    )
                    continue

                if msg_type == "assistant_message":
                    content = msg.get("content", "")
                    no_response = msg.get("no_response", False)
                    done = msg.get("done", True)

                    if content:
                        collected += content

                    if done:
                        if no_response:
                            return None
                        return collected.strip() or None

                elif msg_type == "error":
                    console.print(
                        f"\n[bold red]{msg.get('content', '')}[/bold red]"
                    )
                    return None

    async def _send_and_display(
        self, msg: ChatMessage, *, should_reply: bool = False,
    ) -> None:
        """Send a chat message to daemon and display it."""
        session_id = f"mock:group:{CHANNEL_ID}"
        self._print_chat_message(msg)
        await self.send_message(
            msg.content,
            sender=msg.sender,
            is_owner=(msg.sender == "You"),
            is_private=False,
            should_reply=should_reply,
            channel_id=CHANNEL_ID,
            session_id=session_id,
        )
        self._msg_count += 1

    async def _flush_trigger(self) -> str | None:
        """Send a flush (empty content) to trigger agent evaluation."""
        session_id = f"mock:group:{CHANNEL_ID}"
        console.print("[dim]>>> Flush: 触发 Agent 评估...[/dim]")
        await self.send_message(
            "",
            sender="",
            is_owner=False,
            is_private=False,
            should_reply=True,
            channel_id=CHANNEL_ID,
            session_id=session_id,
        )
        return await self._wait_for_response()

    # ── Main loop ────────────────────────────────────────────────

    async def run(self) -> None:
        """Run the mock group chat provider."""
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
                f"[green]已连接到 VTuber daemon[/green]\n"
                f"频道: [bold]{CHANNEL_ID}[/bold]\n"
                f"群成员: {', '.join(FAKE_USERS)} + You\n\n"
                f"直接输入消息发送到群聊（不触发回复）\n"
                f"[bold]/flush[/bold]  手动触发 Agent 评估上下文\n"
                f"[bold]/mention[/bold] 模拟 @Agent 立即回复\n"
                f"[bold]/seed[/bold]   注入一组预置对话\n"
                f"[bold]/quit[/bold]   退出",
                title="Mock Group Chat",
                border_style="green",
            )
        )
        console.print()

        try:
            while self.running:
                try:
                    user_input = await self.session.prompt_async(
                        HTML(
                            "<ansigreen><b>You</b></ansigreen>"
                            "<ansigray> › </ansigray>"
                        ),
                    )
                except (EOFError, KeyboardInterrupt):
                    await self.disconnect()
                    return

                text = user_input.strip()

                if text.lower() in ("/quit", "/exit"):
                    await self.disconnect()
                    console.print("\n[dim]已断开连接[/dim]")
                    return

                if text.lower() == "/flush":
                    response = await self._flush_trigger()
                    self._show_response(response)
                    continue

                if text.lower() == "/seed":
                    seed = self._get_seed_messages()
                    for msg in seed:
                        await self._send_and_display(msg)
                    console.print(f"[dim]已注入 {len(seed)} 条预置消息[/dim]")
                    continue

                if text.lower().startswith("/mention"):
                    # /mention [text] — simulate a mention that triggers reply
                    mention_text = text[len("/mention"):].strip() or "你觉得呢？"
                    msg = ChatMessage(sender="You", content=mention_text)
                    await self._send_and_display(msg, should_reply=True)
                    response = await self._wait_for_response()
                    self._show_response(response)
                    continue

                if not text:
                    text = random.choice(["嗯嗯", "哈哈", "有道理", "确实"])

                msg = ChatMessage(sender="You", content=text)
                await self._send_and_display(msg)

        finally:
            await self.disconnect()
            console.print("\n[dim]已断开连接[/dim]")

    def _show_response(self, response: str | None) -> None:
        """Display agent response or no-response notice."""
        if response:
            console.print()
            console.print(
                Panel(
                    Markdown(response),
                    title="[bold cyan]Agent[/bold cyan]",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )
        else:
            console.print()
            console.print("[dim italic]Agent 选择不回复[/dim italic]")
        console.print()


def main():
    """Main entry point for mock group chat provider."""
    try:
        provider = MockGroupProvider()
        asyncio.run(provider.run())
    except KeyboardInterrupt:
        console.print("\n[dim]再见！[/dim]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]客户端错误：{e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
