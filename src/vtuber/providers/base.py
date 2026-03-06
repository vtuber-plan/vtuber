"""Provider base class - abstract daemon communication + platform adaptation."""

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from vtuber.daemon.protocol import encode_message, decode_message, MessageType
from vtuber.config import get_socket_path

logger = logging.getLogger("vtuber.provider")


@dataclass
class ChatMessage:
    """A single message in a conversation context."""

    sender: str
    content: str


class Provider(ABC):
    """Base class for all platform providers.

    Handles daemon socket communication. Subclasses implement
    platform-specific message rendering and user input.
    """

    provider_type: str = "base"

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or get_socket_path()
        self.provider_id = f"{self.provider_type}-{uuid.uuid4().hex[:8]}"
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.running = False
        self._reader_task: asyncio.Task | None = None

    # ── Daemon Communication ─────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to daemon and register as a provider."""
        if not self.socket_path.exists():
            return False
        try:
            self.reader, self.writer = await asyncio.open_unix_connection(
                str(self.socket_path)
            )
            self.running = True
            # Register with the gateway
            await self._send({
                "type": MessageType.REGISTER,
                "provider": self.provider_type,
                "provider_id": self.provider_id,
            })
            # Start background socket reader
            self._reader_task = asyncio.create_task(self._read_loop())
            return True
        except Exception as e:
            logger.error("Connection failed: %s", e)
            return False

    async def disconnect(self) -> None:
        """Disconnect from daemon."""
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

    async def send_message(
        self,
        content: str,
        *,
        sender: str = "owner",
        is_owner: bool = True,
        is_private: bool = True,
        channel_id: str | None = None,
        context: list[ChatMessage] | None = None,
    ) -> None:
        """Send a user message to the daemon.

        Args:
            content: The message text.
            sender: Display name of the message sender.
            is_owner: Whether the sender is the agent's primary user.
            is_private: True for DM/CLI, False for group chats.
            channel_id: Unique channel identifier for group chats.
            context: Recent conversation context (for group chats).
        """
        msg: dict = {
            "type": MessageType.USER_MESSAGE,
            "content": content,
            "sender": sender,
            "is_owner": is_owner,
            "is_private": is_private,
        }
        if channel_id is not None:
            msg["channel_id"] = channel_id
        if context:
            msg["context"] = [
                {"sender": m.sender, "content": m.content} for m in context
            ]
        await self._send(msg)

    async def _send(self, msg: dict) -> None:
        """Send a raw message dict to daemon."""
        if not self.writer:
            return
        try:
            data = encode_message(msg)
            self.writer.write(data.encode("utf-8"))
            await self.writer.drain()
        except Exception as e:
            logger.error("Send failed: %s", e)

    async def _read_loop(self) -> None:
        """Background task: read from socket and dispatch messages."""
        if not self.reader:
            return
        buffer = b""
        try:
            while self.running:
                data = await self.reader.read(4096)
                if not data:
                    self.running = False
                    await self.on_disconnected()
                    break
                buffer += data
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    line = raw_line.decode("utf-8", errors="replace")
                    if line.strip():
                        try:
                            msg = decode_message(line)
                            await self._dispatch(msg)
                        except Exception:
                            pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.running:
                logger.error("Read error: %s", e)

    async def _dispatch(self, msg: dict) -> None:
        """Route a daemon message to the appropriate callback."""
        msg_type = msg.get("type")
        if msg_type == MessageType.ASSISTANT_MESSAGE:
            await self.on_response(
                msg.get("content", ""),
                msg.get("is_final", False),
            )
        elif msg_type == MessageType.PROGRESS:
            await self.on_progress(msg.get("tool", ""))
        elif msg_type == MessageType.ERROR:
            await self.on_error(msg.get("content", ""))
        elif msg_type == MessageType.HEARTBEAT_MESSAGE:
            await self.on_heartbeat(
                msg.get("content", ""),
                msg.get("is_final", False),
            )
        elif msg_type == MessageType.TASK_MESSAGE:
            await self.on_task(
                msg.get("content", ""),
                msg.get("task", ""),
                msg.get("is_final", False),
            )
        elif msg_type == MessageType.PONG:
            pass  # silently ignore

    # ── Platform Callbacks (subclasses implement) ────────────────

    @abstractmethod
    async def on_response(self, content: str, is_final: bool) -> None:
        """Handle assistant response chunk or final message."""
        ...

    @abstractmethod
    async def on_progress(self, tool: str) -> None:
        """Handle progress update (agent is using a tool)."""
        ...

    @abstractmethod
    async def on_error(self, error: str) -> None:
        """Handle error from daemon."""
        ...

    @abstractmethod
    async def on_heartbeat(self, content: str, is_final: bool) -> None:
        """Handle heartbeat message from agent."""
        ...

    @abstractmethod
    async def on_task(self, content: str, task: str, is_final: bool) -> None:
        """Handle scheduled task result."""
        ...

    async def on_disconnected(self) -> None:
        """Called when daemon connection is lost. Override for custom handling."""
        pass

    # ── Main Loop ────────────────────────────────────────────────

    @abstractmethod
    async def run(self) -> None:
        """Main event loop for this provider."""
        ...
