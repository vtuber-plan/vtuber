"""CLI chat interface - terminal-based chat for development and testing."""

from collections.abc import AsyncIterator

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from vtuber.interface.base import ChatInterface

console = Console()


class CLIInterface(ChatInterface):
    """Interactive CLI chat interface with rich rendering."""

    def __init__(self, prompt: str = "> ") -> None:
        self.prompt = prompt
        self.session = PromptSession()

    async def receive_message(self) -> str:
        try:
            return self.session.prompt(
                HTML("<ansigreen><b>You</b></ansigreen> <ansigray>›</ansigray> ")
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return "/quit"

    async def send_message(self, text: str) -> None:
        console.print()
        console.print(
            Panel(
                Markdown(text),
                title="[bold cyan]AI[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        console.print()

    async def send_typing(self) -> None:
        console.print("[dim]思考中...[/dim]", end="\r")

    async def run(self) -> AsyncIterator[str]:
        while True:
            msg = await self.receive_message()
            if msg in ("/quit", "/exit"):
                break
            if not msg:
                continue
            yield msg
