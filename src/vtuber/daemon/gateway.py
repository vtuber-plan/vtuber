"""Gateway - manages provider connections and message routing."""

import asyncio
import logging
from dataclasses import dataclass, field

from vtuber.daemon.protocol import encode_message, MessageType

logger = logging.getLogger("vtuber.daemon")


@dataclass
class ProviderConnection:
    """A single provider connection to the gateway."""

    provider_type: str  # "cli", "discord", "telegram", ...
    provider_id: str
    writer: asyncio.StreamWriter

    async def send(self, msg: dict) -> bool:
        """Send a message to this provider. Returns False if send failed."""
        try:
            data = encode_message(msg)
            self.writer.write(data.encode("utf-8"))
            await self.writer.drain()
            return True
        except Exception as e:
            logger.debug(
                "Failed to send to %s/%s: %s",
                self.provider_type,
                self.provider_id,
                e,
            )
            return False

    async def close(self) -> None:
        """Close the connection."""
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass

    @property
    def info(self) -> str:
        return f"{self.provider_type}/{self.provider_id}"


@dataclass
class Gateway:
    """Manages provider connections and routes messages."""

    connections: dict[str, ProviderConnection] = field(default_factory=dict)

    async def register(self, conn: ProviderConnection) -> None:
        """Register a new provider connection."""
        old = self.connections.get(conn.provider_id)
        if old is not None:
            logger.warning(
                "Replacing existing connection %s", old.info
            )
            await old.close()
        self.connections[conn.provider_id] = conn
        logger.info(
            "Provider registered: %s (total=%d)",
            conn.info,
            len(self.connections),
        )

    async def unregister(self, provider_id: str) -> None:
        """Remove a provider connection."""
        conn = self.connections.pop(provider_id, None)
        if conn:
            await conn.close()
            logger.info(
                "Provider unregistered: %s (remaining=%d)",
                conn.info,
                len(self.connections),
            )

    async def send_to(self, provider_id: str, msg: dict) -> bool:
        """Send a message to a specific provider. Returns False if not found or failed."""
        conn = self.connections.get(provider_id)
        if conn is None:
            return False
        ok = await conn.send(msg)
        if not ok:
            await self.unregister(provider_id)
        return ok

    async def broadcast(self, msg: dict) -> None:
        """Send a message to all connected providers."""
        if not self.connections:
            return
        disconnected = []
        for pid, conn in self.connections.items():
            ok = await conn.send(msg)
            if not ok:
                disconnected.append(pid)
        for pid in disconnected:
            self.connections.pop(pid, None)
            logger.info("Removed dead connection: %s", pid)

    async def close_all(self) -> None:
        """Close all connections."""
        for conn in self.connections.values():
            await conn.close()
        self.connections.clear()
