"""CLI chat interface - terminal-based chat for development and testing."""

import sys
from collections.abc import AsyncIterator

from vtuber.interface.base import ChatInterface


class CLIInterface(ChatInterface):
    """Interactive CLI chat interface."""

    def __init__(self, prompt: str = "> ") -> None:
        self.prompt = prompt

    async def receive_message(self) -> str:
        try:
            return input(self.prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return "/quit"

    async def send_message(self, text: str) -> None:
        print(text, flush=True)

    async def send_typing(self) -> None:
        print("...", end="\r", flush=True)

    async def run(self) -> AsyncIterator[str]:
        while True:
            msg = await self.receive_message()
            if msg in ("/quit", "/exit"):
                break
            if not msg:
                continue
            yield msg
