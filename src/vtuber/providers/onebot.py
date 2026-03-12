"""OneBot v11 provider — connects to an OneBot v11 implementation via WebSocket."""

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field

from rich.console import Console

from vtuber.config import get_config
from vtuber.providers.base import ChatMessage, Provider

logger = logging.getLogger("vtuber.provider.onebot")
console = Console()


@dataclass
class _PendingResponse:
    """Buffer for streamed assistant responses."""

    reply_to: str  # "private" or "group"
    user_id: int | None = None
    group_id: int | None = None
    chunks: list[str] = field(default_factory=list)


def _extract_text(message) -> str:
    """Extract plain text from an OneBot message (string or segment array)."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
        return "".join(parts)
    return str(message)


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
        group_batch_size: Forward to agent every N messages (0 = disabled)
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
        self._group_batch_size: int = int(cfg.get("group_batch_size", 0))
        self._bot_names: list[str] = [str(n) for n in cfg.get("bot_names", []) if n]
        self._stream_intermediate: bool = bool(cfg.get("stream_intermediate", False))

        self._ws = None  # websockets connection
        self._ws_task: asyncio.Task | None = None
        self._pending: dict[str, _PendingResponse] = {}  # session_id -> buffer
        self._group_context: dict[int, list[ChatMessage]] = {}  # group_id -> recent msgs
        self._group_unseen: dict[int, int] = {}  # group_id -> messages since last forward
        self._self_id: int | None = None  # bot's own QQ ID (from lifecycle event)
        self._action_echo: int = 0  # echo counter for action requests

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
                    await self._handle_onebot_event(event)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from OneBot: %s", raw[:200])
                except Exception as e:
                    logger.error("Error handling OneBot event: %s", e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.running:
                logger.error("OneBot WebSocket error: %s", e)
                self.running = False

    async def _send_onebot_action(self, action: str, params: dict, **kwargs) -> None:
        """Send an action request to OneBot."""
        if not self._ws:
            return
        self._action_echo += 1
        payload = {"action": action, "params": params, "echo": str(self._action_echo)}
        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error("Failed to send OneBot action %s: %s", action, e)

    # ── Event Handling ────────────────────────────────────────────

    async def _handle_onebot_event(self, event: dict) -> None:
        """Dispatch an incoming OneBot event."""
        post_type = event.get("post_type")

        if post_type == "meta_event":
            await self._handle_meta_event(event)
        elif post_type == "message":
            await self._handle_message_event(event)

    async def _handle_meta_event(self, event: dict) -> None:
        """Handle meta events (lifecycle, heartbeat)."""
        meta_type = event.get("meta_event_type")
        if meta_type == "lifecycle":
            self._self_id = event.get("self_id")
            sub = event.get("sub_type", "")
            logger.info("OneBot lifecycle: %s (self_id=%s)", sub, self._self_id)
        # heartbeat events are silently ignored

    async def _handle_message_event(self, event: dict) -> None:
        """Handle incoming message events (private & group)."""
        message_type = event.get("message_type")
        user_id = event.get("user_id")
        raw_message = event.get("raw_message", "")
        message = event.get("message", raw_message)
        text = _extract_text(message).strip()

        if not text or user_id == self._self_id:
            return

        # Whitelist filtering — owner always passes
        is_owner = self.owner_id and str(user_id) == self.owner_id

        if message_type == "private":
            if not is_owner and self._user_whitelist and str(user_id) not in self._user_whitelist:
                return
        elif message_type == "group":
            group_id = event.get("group_id")
            if self._group_whitelist and str(group_id) not in self._group_whitelist:
                return

        sender_info = event.get("sender", {})
        nickname = (
            sender_info.get("card")  # group card first
            or sender_info.get("nickname")
            or str(user_id)
        )

        if message_type == "private":
            session_id = f"onebot:private:{user_id}"
            self._pending[session_id] = _PendingResponse(
                reply_to="private", user_id=user_id,
            )
            await self.send_message(
                text,
                sender=nickname,
                is_owner=is_owner,
                is_private=True,
                session_id=session_id,
            )
            logger.debug("Private msg from %s(%s): %s", nickname, user_id, text[:50])

        elif message_type == "group":
            group_id = event.get("group_id")
            if not group_id:
                return

            # Maintain group context ring buffer
            ctx = self._group_context.setdefault(group_id, [])
            ctx.append(ChatMessage(sender=nickname, content=text))
            limit = get_config().group_context_limit
            if len(ctx) > limit:
                self._group_context[group_id] = ctx[-limit:]

            # Track unseen message count
            self._group_unseen[group_id] = self._group_unseen.get(group_id, 0) + 1

            # Determine whether to forward to daemon
            should_forward = False

            # 1. Check if bot is @-mentioned (CQ at segment)
            if isinstance(message, list):
                for seg in message:
                    if (
                        isinstance(seg, dict)
                        and seg.get("type") == "at"
                        and str(seg.get("data", {}).get("qq")) == str(self._self_id)
                    ):
                        should_forward = True
                        break

            # 2. Check if bot name is mentioned in text
            if not should_forward and self._bot_names:
                text_lower = text.lower()
                for name in self._bot_names:
                    if name.lower() in text_lower:
                        should_forward = True
                        break

            # 3. Check if accumulated messages reached batch threshold
            if (
                not should_forward
                and self._group_batch_size > 0
                and self._group_unseen[group_id] >= self._group_batch_size
            ):
                should_forward = True

            if not should_forward:
                return

            # Reset unseen counter on forward
            self._group_unseen[group_id] = 0

            session_id = f"onebot:group:{group_id}"
            # Use recent context (excluding the trigger message itself)
            context = list(self._group_context.get(group_id, []))[:-1]

            self._pending[session_id] = _PendingResponse(
                reply_to="group", group_id=group_id,
            )
            await self.send_message(
                text,
                sender=nickname,
                is_owner=is_owner,
                is_private=False,
                channel_id=str(group_id),
                session_id=session_id,
                context=context[-get_config().group_context_limit:],
            )
            logger.debug(
                "Group msg from %s(%s) in %s: %s",
                nickname, user_id, group_id, text[:50],
            )

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
                await self._send_onebot_action("send_private_msg", {
                    "user_id": int(self.owner_id),
                    "message": content,
                })

        elif msg_type == MessageType.TASK_MESSAGE:
            content = msg.get("content", "")
            task = msg.get("task", "")
            done = msg.get("done", True)
            if done and content.strip() and self.owner_id:
                text = f"[定时任务] {task}\n{content}" if task else content
                await self._send_onebot_action("send_private_msg", {
                    "user_id": int(self.owner_id),
                    "message": text,
                })

    # ── Abstract method stubs (unused — _dispatch is overridden) ──

    async def on_response(self, content: str, *, done: bool) -> None:
        pass

    async def on_progress(self, tool: str) -> None:
        pass

    async def on_error(self, error: str) -> None:
        pass

    async def on_heartbeat(self, content: str) -> None:
        pass

    async def on_task(self, content: str, task: str, *, done: bool) -> None:
        pass

    async def on_disconnected(self) -> None:
        """Handle daemon disconnection."""
        logger.warning("Daemon connection lost")
        self.running = False

    # ── Reply Helper ──────────────────────────────────────────────

    async def _send_reply(self, pending: _PendingResponse, text: str) -> None:
        """Send a reply through OneBot."""
        if pending.reply_to == "private" and pending.user_id:
            await self._send_onebot_action("send_private_msg", {
                "user_id": pending.user_id,
                "message": text,
            })
        elif pending.reply_to == "group" and pending.group_id:
            await self._send_onebot_action("send_group_msg", {
                "group_id": pending.group_id,
                "message": text,
            })

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

        console.print(f"[green]已连接到 VTuber daemon[/green]")

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

        # Start OneBot reader loop
        self._ws_task = asyncio.create_task(self._onebot_read_loop())

        try:
            # Wait until either connection dies
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


if __name__ == "__main__":
    main()
