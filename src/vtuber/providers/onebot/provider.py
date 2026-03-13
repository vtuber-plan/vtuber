"""OneBot v11 provider — connects to an OneBot v11 implementation via WebSocket."""

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from vtuber.config import get_config
from vtuber.providers.base import Provider

from .events import handle_onebot_event
from .render import render_text_as_image, should_render_as_image

logger = logging.getLogger("vtuber.provider.onebot")
console = Console()


@dataclass
class _PendingResponse:
    """Buffer for streamed assistant responses."""

    reply_to: str  # "private" or "group"
    user_id: int | None = None
    group_id: int | None = None
    chunks: list[str] = field(default_factory=list)


class OneBotProvider(Provider):
    """OneBot v11 WebSocket (forward) provider.

    Connects to an OneBot implementation's WebSocket endpoint, receives
    message events, forwards them to the vtuber daemon, and sends
    assistant responses back through the OneBot API.

    Config (in config.yaml under providers.onebot):
        ws_url: WebSocket URL (default: ws://127.0.0.1:6700)
        access_token: Optional access token for authentication
        owner_id: QQ user ID of the bot owner
        bot_names: List of names the bot responds to in group chat
        text2img_url: Text2Image service URL (empty = disabled)
        long_text_threshold: Character count threshold for image rendering
    """

    provider_type = "onebot"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.provider_id = "onebot"  # stable singleton ID

        cfg = get_config().providers.get("onebot", {})
        self.ws_url: str = cfg.get("ws_url", "ws://127.0.0.1:6700")
        self.access_token: str = cfg.get("access_token", "")
        self.owner_id: str = str(cfg.get("owner_id", ""))
        self._user_whitelist: set[str] = {str(u) for u in cfg.get("user_whitelist", [])}
        self._group_whitelist: set[str] = {str(g) for g in cfg.get("group_whitelist", [])}
        self._group_reply_delay: int = int(cfg.get("group_reply_delay", 120))
        self._bot_names: list[str] = [str(n) for n in cfg.get("bot_names", []) if n]
        self._stream_intermediate: bool = bool(cfg.get("stream_intermediate", False))
        self._text2img_url: str = cfg.get("text2img_url", "").rstrip("/")
        self._long_text_threshold: int = int(cfg.get("long_text_threshold", 300))

        self._ws = None  # websockets connection
        self._ws_task: asyncio.Task | None = None
        self._pending: dict[str, _PendingResponse] = {}  # session_id -> buffer
        self._group_debounce_tasks: dict[int, asyncio.Task] = {}  # group_id -> timer
        self._self_id: int | None = None  # bot's own QQ ID (from lifecycle event)
        self._action_echo: int = 0  # echo counter for action requests
        self._api_futures: dict[str, asyncio.Future] = {}  # echo -> Future

    # ── OneBot WebSocket ──────────────────────────────────────────

    async def _connect_onebot(self) -> bool:
        """Connect to the OneBot WebSocket endpoint."""
        try:
            import websockets
        except ImportError:
            console.print(
                "[red]websockets 未安装[/red]\n"
                "请运行: [bold]uv add websockets[/bold]"
            )
            return False

        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        try:
            self._ws = await websockets.connect(
                self.ws_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            )
            logger.info("Connected to OneBot at %s", self.ws_url)
            return True
        except Exception as e:
            logger.error("Failed to connect to OneBot: %s", e)
            return False

    async def _onebot_read_loop(self) -> None:
        """Read events from OneBot WebSocket and dispatch them."""
        try:
            async for raw in self._ws:
                try:
                    event = json.loads(raw)
                    await handle_onebot_event(self, event)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from OneBot: %s", raw[:200])
                except Exception as e:
                    logger.error("Error handling OneBot event: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.running:
                logger.error("OneBot WebSocket disconnected: %s", e)
        finally:
            # Cancel stale API futures
            for fut in self._api_futures.values():
                if not fut.done():
                    fut.cancel()
            self._api_futures.clear()

    async def _reconnect_loop(self) -> None:
        """Reconnect to OneBot WebSocket with exponential backoff."""
        delay = 2.0
        max_delay = 60.0

        while self.running:
            logger.info("Attempting OneBot reconnect in %.1fs...", delay)
            await asyncio.sleep(delay)

            if not self.running:
                break

            if await self._connect_onebot():
                logger.info("OneBot reconnected successfully")
                console.print("[green]OneBot 已重新连接[/green]")
                delay = 2.0  # reset backoff
                await self._onebot_read_loop()
                # If read loop exits and we're still running, loop will retry
            else:
                delay = min(delay * 2, max_delay)

    # ── OneBot API ────────────────────────────────────────────────

    async def send_onebot_action(
        self, action: str, params: dict, *, wait: bool = False, timeout: float = 10.0,
    ) -> dict | None:
        """Send an action request to OneBot.

        If *wait* is ``True``, block until the response is received (correlated
        by ``echo``).  Returns the full response dict, or ``None`` on timeout.
        """
        if not self._ws:
            return None
        self._action_echo += 1
        echo_str = str(self._action_echo)
        payload = {"action": action, "params": params, "echo": echo_str}

        future: asyncio.Future | None = None
        if wait:
            future = asyncio.get_running_loop().create_future()
            self._api_futures[echo_str] = future

        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error("Failed to send OneBot action %s: %s", action, e)
            if future:
                self._api_futures.pop(echo_str, None)
            return None

        if future:
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except TimeoutError:
                logger.warning("OneBot action %s timed out", action)
                return None
            finally:
                self._api_futures.pop(echo_str, None)
                if not future.done():
                    future.cancel()
        return None

    # ── Daemon Message Dispatch (override for session routing) ──

    async def _dispatch(self, msg: dict) -> None:
        """Route daemon messages using session_id for correct reply target."""
        from vtuber.daemon.protocol import MessageType

        msg_type = msg.get("type")
        session_id = msg.get("session_id", "")

        if msg_type == MessageType.ASSISTANT_MESSAGE:
            content = msg.get("content", "")
            done = msg.get("done", True)
            no_response = msg.get("no_response", False)

            pending = self._pending.get(session_id)
            if pending is None:
                return

            if content:
                if self._stream_intermediate:
                    pending.chunks.append(content)
                else:
                    pending.chunks = [content]

            if done:
                if not no_response:
                    full_text = "".join(pending.chunks).strip()
                    if full_text:
                        await self._send_reply(pending, full_text)
                del self._pending[session_id]

        elif msg_type == MessageType.PROGRESS:
            pass  # not surfaced to chat platforms

        elif msg_type == MessageType.ERROR:
            logger.error("Daemon error: %s", msg.get("content", ""))

        elif msg_type == MessageType.HEARTBEAT_MESSAGE:
            content = msg.get("content", "")
            if content.strip() and self.owner_id:
                await self.send_onebot_action("send_private_msg", {
                    "user_id": int(self.owner_id),
                    "message": content,
                })

        elif msg_type == MessageType.TASK_MESSAGE:
            content = msg.get("content", "")
            task = msg.get("task", "")
            done = msg.get("done", True)
            if done and content.strip() and self.owner_id:
                text = f"[定时任务] {task}\n{content}" if task else content
                await self.send_onebot_action("send_private_msg", {
                    "user_id": int(self.owner_id),
                    "message": text,
                })

    async def on_disconnected(self) -> None:
        """Handle daemon disconnection."""
        logger.warning("Daemon connection lost")
        self.running = False

    # ── Reply Helper ──────────────────────────────────────────────

    # File extensions that should be sent as file uploads in private chat
    _SENDABLE_EXTENSIONS = frozenset((
        ".pdf", ".markdown", ".md", ".txt",
        ".ppt", ".pptx", ".doc", ".docx",
        ".wav", ".mp3",
        ".jpg", ".jpeg", ".gif", ".png",
    ))

    # Match absolute paths like /home/user/file.txt or ~/file.txt
    _FILE_PATH_RE = re.compile(r"(?:~|/)[^\s\]\)]+")

    async def _send_reply(self, pending: _PendingResponse, text: str) -> None:
        """Send a reply through OneBot, rendering as image if needed.

        For private messages, file paths with supported extensions are
        extracted and sent as file uploads via ``upload_private_file``.
        """
        # In private chat, detect and send file paths as file uploads
        if pending.reply_to == "private" and pending.user_id:
            file_paths, remaining_text = self._extract_file_paths(text)
            for fp in file_paths:
                await self._upload_private_file(pending.user_id, fp)
            text = remaining_text

        if not text:
            return

        message: str | list[dict] = text

        if should_render_as_image(
            text,
            threshold=self._long_text_threshold,
            enabled=bool(self._text2img_url),
        ):
            image_url = await render_text_as_image(text, self._text2img_url)
            if image_url:
                message = [{"type": "image", "data": {"file": image_url}}]

        if pending.reply_to == "private" and pending.user_id:
            await self.send_onebot_action("send_private_msg", {
                "user_id": pending.user_id,
                "message": message,
            })
        elif pending.reply_to == "group" and pending.group_id:
            await self.send_onebot_action("send_group_msg", {
                "group_id": pending.group_id,
                "message": message,
            })

    def _extract_file_paths(self, text: str) -> tuple[list[Path], str]:
        """Extract valid sendable file paths from text.

        Returns (list_of_paths, remaining_text_with_paths_removed).
        """
        found: list[Path] = []
        spans_to_remove: list[tuple[int, int]] = []

        for m in self._FILE_PATH_RE.finditer(text):
            raw = m.group()
            p = Path(raw).expanduser()
            if p.is_file() and p.suffix.lower() in self._SENDABLE_EXTENSIONS:
                found.append(p)
                spans_to_remove.append(m.span())

        if not spans_to_remove:
            return [], text

        # Remove matched paths from text (reverse order to preserve indices)
        parts = list(text)
        for start, end in reversed(spans_to_remove):
            parts[start:end] = []
        remaining = "".join(parts).strip()

        return found, remaining

    async def _upload_private_file(self, user_id: int, path: Path) -> None:
        """Upload a file to a private chat via upload_private_file API."""
        resp = await self.send_onebot_action(
            "upload_private_file",
            {
                "user_id": user_id,
                "file": str(path),
                "name": path.name,
            },
            wait=True,
            timeout=30.0,
        )
        if resp and resp.get("status") == "ok":
            logger.info("Uploaded file to user %s: %s", user_id, path.name)
        else:
            logger.warning(
                "Failed to upload file %s to user %s: %s",
                path.name, user_id, resp,
            )

    # ── Main Loop ─────────────────────────────────────────────────

    async def run(self) -> None:
        """Main event loop: connect to both daemon and OneBot, then wait."""
        # Connect to daemon
        if not await self.connect():
            console.print(
                "[red]无法连接到 VTuber daemon[/red]\n"
                "请先启动: [bold]vtuber start[/bold]"
            )
            return

        console.print("[green]已连接到 VTuber daemon[/green]")

        # Connect to OneBot
        if not await self._connect_onebot():
            console.print(
                f"[red]无法连接到 OneBot ({self.ws_url})[/red]\n"
                "请确认 OneBot 实现已启动并配置了正向 WebSocket。"
            )
            await self.disconnect()
            return

        console.print(
            f"[green]已连接到 OneBot[/green] ({self.ws_url})\n"
            f"Owner ID: {self.owner_id or '(未设置)'}\n"
            f"等待消息中..."
        )

        # Start OneBot reader loop with reconnect support
        async def _read_then_reconnect() -> None:
            await self._onebot_read_loop()
            if self.running:
                await self._reconnect_loop()

        self._ws_task = asyncio.create_task(_read_then_reconnect())

        try:
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up all connections."""
        self.running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        # Cancel stale API futures
        for fut in self._api_futures.values():
            if not fut.done():
                fut.cancel()
        self._api_futures.clear()
        # Cancel active debounce timers
        for task in self._group_debounce_tasks.values():
            task.cancel()
        self._group_debounce_tasks.clear()
        await self.disconnect()
        console.print("[dim]OneBot provider 已停止[/dim]")


def main():
    """Entry point for the OneBot provider."""
    try:
        provider = OneBotProvider()
        asyncio.run(provider.run())
    except KeyboardInterrupt:
        console.print("\n[dim]再见！[/dim]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]OneBot provider 错误：{e}[/red]")
        sys.exit(1)
