"""Abstract chat interface - unified abstraction for different frontends."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class ChatInterface(ABC):
    """Base class for all chat interfaces (CLI, Web UI, social platforms, etc.)."""

    @abstractmethod
    async def receive_message(self) -> str:
        """Wait for and return the next user message."""
        ...

    @abstractmethod
    async def send_message(self, text: str) -> None:
        """Send a text message to the user."""
        ...

    @abstractmethod
    async def send_typing(self) -> None:
        """Signal that the agent is processing/typing."""
        ...

    @abstractmethod
    async def run(self) -> AsyncIterator[str]:
        """Main loop yielding user messages as they arrive."""
        ...
