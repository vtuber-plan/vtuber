"""NapCat provider — connects to NapCat via napcat-sdk typed client."""

import asyncio
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from napcat import (
    At,
    Forward,
    FriendPokeEvent,
    GroupMessageEvent,
    GroupPokeEvent,
    Image,
    Message,
    NapCatClient,
    NapCatEvent,
    OnlineFileNoticeEvent,
    PrivateMessageEvent,
    Record,
    Reply,
    Text,
    UnknownMessageSegment,
)

from vtuber.config import get_config
from vtuber.providers.base import Provider
from vtuber.providers.render import render_text_as_image, should_render_as_image

logger = logging.getLogger("vtuber.provider.napcat")
console = Console()


@dataclass
class _PendingResponse:
    """Buffer for streamed assistant responses."""

    reply_to: str  # "private" or "group"
    user_id: int | None = None
    group_id: int | None = None
    chunks: list[str] = field(default_factory=list)


class NapCatProvider(Provider):
    """NapCat provider using napcat-sdk typed client.

    Uses the same config section as the OneBot provider (providers.onebot).
    """

    provider_type = "napcat"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.provider_id = "napcat"

        cfg = get_config().providers.get("onebot", {})
        self._ws_url: str = cfg.get("ws_url", "ws://127.0.0.1:6700")
        self._access_token: str = cfg.get("access_token", "")
        self._owner_id: str = str(cfg.get("owner_id", ""))
        self._user_whitelist: set[str] = {str(u) for u in cfg.get("user_whitelist", [])}
        self._group_whitelist: set[str] = {str(g) for g in cfg.get("group_whitelist", [])}
        self._group_reply_delay: int = int(cfg.get("group_reply_delay", 120))
        self._bot_names: list[str] = [str(n) for n in cfg.get("bot_names", []) if n]
        self._stream_intermediate: bool = bool(cfg.get("stream_intermediate", False))
        self._text2img_url: str = cfg.get("text2img_url", "https://t2i.soulter.top/text2img").rstrip("/")
        self._long_text_threshold: int = int(cfg.get("long_text_threshold", 200))

        self._client: NapCatClient | None = None
        self._event_task: asyncio.Task | None = None
        self._pending: dict[str, _PendingResponse] = {}
        self._group_debounce_tasks: dict[int, asyncio.Task] = {}

    # ── Event Loop ────────────────────────────────────────────────

    async def _event_loop(self) -> None:
        """Iterate over napcat-sdk events and dispatch them."""
        assert self._client is not None
        try:
            async for event in self._client:
                try:
                    await self._handle_event(event)
                except Exception as e:
                    logger.error("Error handling event: %s", e, exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.running:
                logger.error("NapCat event loop error: %s", e)

    async def _handle_event(self, event: NapCatEvent) -> None:
        """Dispatch a typed napcat-sdk event."""
        match event:
            case GroupMessageEvent():
                await self._handle_group_message(event)
            case PrivateMessageEvent():
                await self._handle_private_message(event)
            case GroupPokeEvent(target_id=target_id) if target_id == self._client.self_id:
                await self._handle_group_poke(event)
            case FriendPokeEvent(target_id=target_id) if target_id == self._client.self_id:
                await self._handle_friend_poke(event)
            case OnlineFileNoticeEvent():
                await self._handle_file_upload(event)
            case _:
                pass

    # ── Private Messages ──────────────────────────────────────────

    async def _handle_private_message(self, event: PrivateMessageEvent) -> None:
        user_id = int(event.user_id)
        if user_id == self._client.self_id:
            return

        text = await self._extract_text(event.message, is_private=True)
        if not text:
            return

        is_owner = self._owner_id and str(user_id) == self._owner_id
        if not is_owner and self._user_whitelist and str(user_id) not in self._user_whitelist:
            return

        nickname = event.sender.card or event.sender.nickname or str(user_id)
        session_id = f"napcat:private:{user_id}"

        self._pending[session_id] = _PendingResponse(reply_to="private", user_id=user_id)
        await self.send_message(
            text,
            sender=nickname,
            is_owner=is_owner,
            is_private=True,
            session_id=session_id,
        )
        logger.debug("Private msg from %s(%s): %s", nickname, user_id, text[:50])

    # ── Group Messages ────────────────────────────────────────────

    async def _handle_group_message(self, event: GroupMessageEvent) -> None:
        user_id = int(event.user_id)
        if user_id == self._client.self_id:
            return

        text = await self._extract_text(event.message, is_private=False)
        if not text:
            return

        group_id = event.group_id
        if self._group_whitelist and str(group_id) not in self._group_whitelist:
            return

        is_owner = self._owner_id and str(user_id) == self._owner_id
        nickname = event.sender.card or event.sender.nickname or str(user_id)
        session_id = f"napcat:group:{group_id}"
        is_mention = self._check_mention(event)

        if is_mention:
            self._cancel_debounce(group_id)
            self._pending[session_id] = _PendingResponse(reply_to="group", group_id=group_id)
            await self.send_message(
                text,
                sender=nickname,
                is_owner=is_owner,
                is_private=False,
                should_reply=True,
                channel_id=str(group_id),
                session_id=session_id,
            )
        else:
            await self.send_message(
                text,
                sender=nickname,
                is_owner=is_owner,
                is_private=False,
                should_reply=False,
                channel_id=str(group_id),
                session_id=session_id,
            )
            if self._group_reply_delay > 0:
                self._start_debounce(group_id, session_id)

        logger.debug(
            "Group msg from %s(%s) in %s (mention=%s): %s",
            nickname, user_id, group_id, is_mention, text[:50],
        )

    def _check_mention(self, event: GroupMessageEvent) -> bool:
        """Check if the bot is @-mentioned or its name appears in the text."""
        for seg in event.message:
            match seg:
                case At(qq=qq) if str(qq) == str(self._client.self_id):
                    return True
                case _:
                    pass

        if self._bot_names:
            text_lower = event.raw_message.lower()
            for name in self._bot_names:
                if name.lower() in text_lower:
                    return True
        return False

    # ── Group Debounce ────────────────────────────────────────────

    def _cancel_debounce(self, group_id: int) -> None:
        task = self._group_debounce_tasks.pop(group_id, None)
        if task and not task.done():
            task.cancel()

    def _start_debounce(self, group_id: int, session_id: str) -> None:
        self._cancel_debounce(group_id)
        self._group_debounce_tasks[group_id] = asyncio.create_task(
            self._debounce_flush(group_id, session_id)
        )

    async def _debounce_flush(self, group_id: int, session_id: str) -> None:
        try:
            await asyncio.sleep(self._group_reply_delay)
        except asyncio.CancelledError:
            return

        self._group_debounce_tasks.pop(group_id, None)
        if session_id in self._pending:
            return

        self._pending[session_id] = _PendingResponse(reply_to="group", group_id=group_id)
        await self.send_message(
            "",
            sender="",
            is_owner=False,
            is_private=False,
            should_reply=True,
            channel_id=str(group_id),
            session_id=session_id,
        )
        logger.debug("Debounce flush for group %s", group_id)

    # ── Poke Events ───────────────────────────────────────────────

    async def _handle_group_poke(self, event: GroupPokeEvent) -> None:
        group_id = event.group_id
        if self._group_whitelist and str(group_id) not in self._group_whitelist:
            return

        user_id = event.user_id
        is_owner = self._owner_id and str(user_id) == self._owner_id
        nickname = str(user_id)
        session_id = f"napcat:group:{group_id}"

        self._pending[session_id] = _PendingResponse(reply_to="group", group_id=group_id)
        await self.send_message(
            f"[戳一戳] {nickname} 戳了戳你",
            sender=nickname,
            is_owner=is_owner,
            is_private=False,
            channel_id=str(group_id),
            session_id=session_id,
        )

    async def _handle_friend_poke(self, event: FriendPokeEvent) -> None:
        user_id = event.user_id
        is_owner = self._owner_id and str(user_id) == self._owner_id

        if not is_owner and self._user_whitelist and str(user_id) not in self._user_whitelist:
            return

        nickname = str(user_id)
        session_id = f"napcat:private:{user_id}"

        self._pending[session_id] = _PendingResponse(reply_to="private", user_id=user_id)
        await self.send_message(
            f"[戳一戳] {nickname} 戳了戳你",
            sender=nickname,
            is_owner=is_owner,
            is_private=True,
            session_id=session_id,
        )

    # ── File Upload ───────────────────────────────────────────────

    async def _handle_file_upload(self, event: OnlineFileNoticeEvent) -> None:
        from vtuber.providers.onebot.message import download_file

        user_id = event.peer_id
        is_owner = self._owner_id and str(user_id) == self._owner_id

        if not is_owner and self._user_whitelist and str(user_id) not in self._user_whitelist:
            return

        # OnlineFileNoticeEvent has limited info; try raw dict for file details
        raw = event._raw if hasattr(event, "_raw") else {}
        file_info = raw.get("file", {})
        url = file_info.get("url", "")
        filename = file_info.get("name", "")
        file_id = file_info.get("id", "") or file_info.get("file_id", "")

        if not url and file_id and self._client:
            try:
                resp = await self._client.get_private_file_url(file_id=file_id)
                url = getattr(resp, "url", "") or getattr(resp, "private_url", "")
            except Exception:
                try:
                    resp = await self._client.get_file(file_id=file_id)
                    url = getattr(resp, "url", "")
                except Exception:
                    pass

        if not url:
            logger.warning("File upload notice without URL: %s", raw)
            return

        local_path = await download_file(url, filename)
        if not local_path:
            logger.warning("Failed to download uploaded file: %s", filename)
            return

        nickname = str(user_id)
        session_id = f"napcat:private:{user_id}"
        self._pending[session_id] = _PendingResponse(reply_to="private", user_id=user_id)
        await self.send_message(
            f"[文件: {local_path}]",
            sender=nickname,
            is_owner=is_owner,
            is_private=True,
            session_id=session_id,
        )
        logger.info("File upload from %s: %s -> %s", nickname, filename, local_path)

    # ── Message Text Extraction ───────────────────────────────────

    async def _extract_text(
        self,
        segments: Sequence[Message | UnknownMessageSegment],
        *,
        is_private: bool = False,
    ) -> str:
        """Extract text from typed message segments, resolving replies and forwards."""
        from vtuber.providers.onebot.message import download_file

        parts: list[str] = []
        for seg in segments:
            match seg:
                case Text(text=t):
                    parts.append(t)

                case Reply():
                    ctx = await self._fetch_reply_context(seg)
                    if ctx:
                        parts.insert(0, ctx)

                case Forward(id=fwd_id) if fwd_id:
                    ctx = await self._fetch_forward_context(fwd_id)
                    if ctx:
                        parts.append(ctx)

                case Record(url=url, file=filename) if is_private and url:
                    local_path = await download_file(url, filename or "")
                    if local_path:
                        parts.append(f"[语音: {local_path}]")
                    else:
                        parts.append("[语音: 下载失败]")

                case _ if is_private and hasattr(seg, "url") and hasattr(seg, "file"):
                    # Generic file-like segment (File, etc.)
                    url = getattr(seg, "url", "")
                    filename = getattr(seg, "file", "")
                    if url:
                        local_path = await download_file(url, filename or "")
                        if local_path:
                            parts.append(f"[文件: {local_path}]")
                        else:
                            parts.append("[文件: 下载失败]")

                case At():
                    pass  # handled by _check_mention

                case _:
                    pass

        return "".join(parts).strip()

    async def _fetch_reply_context(self, reply: Reply) -> str | None:
        """Fetch original message for a reply segment."""
        if not self._client:
            return None
        msg_id = getattr(reply, "id", None)
        if not msg_id:
            return None
        try:
            resp = await self._client.get_msg(message_id=int(msg_id))
            sender_name = resp.get("sender", {}).get("nickname", "未知") if isinstance(resp, dict) else getattr(resp, "sender", {}).get("nickname", "未知")
            raw_msg = resp.get("raw_message", "") if isinstance(resp, dict) else getattr(resp, "raw_message", "")
            if not raw_msg:
                return None
            if len(raw_msg) > 100:
                raw_msg = raw_msg[:100] + "..."
            return f"[回复 {sender_name}: {raw_msg}]\n"
        except Exception as e:
            logger.debug("Failed to fetch reply context: %s", e)
            return None

    async def _fetch_forward_context(self, forward_id: str) -> str | None:
        """Fetch messages in a forward/merge segment."""
        if not self._client:
            return None
        try:
            resp = await self._client.get_forward_msg(id=forward_id)
            messages = getattr(resp, "messages", None) or []
            if not messages:
                return None
            lines = ["[合并转发]"]
            for node in messages[:20]:
                if isinstance(node, dict):
                    node_data = node.get("data", node)
                    sender = (
                        node_data.get("sender", {}).get("nickname")
                        or node_data.get("nickname")
                        or node_data.get("name")
                        or "未知"
                    )
                    content = node_data.get("content", "")
                    if isinstance(content, str) and content.strip():
                        lines.append(f"{sender}: {content}")
            return "\n".join(lines) + "\n"
        except Exception as e:
            logger.debug("Failed to fetch forward context: %s", e)
            return None

    # ── Daemon Message Dispatch ───────────────────────────────────

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

        elif msg_type == MessageType.ERROR:
            logger.error("Daemon error: %s", msg.get("content", ""))

        elif msg_type == MessageType.HEARTBEAT_MESSAGE:
            content = msg.get("content", "")
            if content.strip() and self._owner_id and self._client:
                await self._client.send_private_msg(
                    user_id=self._owner_id,
                    message=content,
                )

        elif msg_type == MessageType.TASK_MESSAGE:
            content = msg.get("content", "")
            task = msg.get("task", "")
            done = msg.get("done", True)
            if done and content.strip() and self._owner_id and self._client:
                text = f"[定时任务] {task}\n{content}" if task else content
                await self._client.send_private_msg(
                    user_id=self._owner_id,
                    message=text,
                )

    async def on_disconnected(self) -> None:
        logger.warning("Daemon connection lost")
        self.running = False

    # ── Reply Helper ──────────────────────────────────────────────

    _SENDABLE_EXTENSIONS = frozenset((
        ".pdf", ".markdown", ".md", ".txt",
        ".ppt", ".pptx", ".doc", ".docx",
        ".wav", ".mp3",
        ".jpg", ".jpeg", ".gif", ".png",
    ))

    _FILE_PATH_RE = re.compile(r"(?:~|/)[A-Za-z0-9_./@:~-]+(?:/[A-Za-z0-9_./@:~-]+)*")

    async def _send_reply(self, pending: _PendingResponse, text: str) -> None:
        """Send a reply, rendering as image if needed."""
        if not self._client:
            return

        # In private chat, detect and send file paths as file uploads
        if pending.reply_to == "private" and pending.user_id:
            file_paths, remaining = self._extract_file_paths(text)
            for fp in file_paths:
                await self._upload_private_file(pending.user_id, fp)
            text = remaining

        if not text:
            return

        message: str | list[dict] = text

        should_render = should_render_as_image(
            text,
            threshold=self._long_text_threshold,
            enabled=bool(self._text2img_url),
        )
        if should_render:
            image_url = await render_text_as_image(text, self._text2img_url)
            if image_url:
                message = [{"type": "image", "data": {"file": image_url}}]

        if pending.reply_to == "private" and pending.user_id:
            await self._client.send_private_msg(
                user_id=str(pending.user_id),
                message=message,
            )
        elif pending.reply_to == "group" and pending.group_id:
            await self._client.send_group_msg(
                group_id=str(pending.group_id),
                message=message,
            )

    def _extract_file_paths(self, text: str) -> tuple[list[Path], str]:
        """Extract valid sendable file paths from text."""
        found: list[Path] = []
        spans_to_remove: list[tuple[int, int]] = []

        for m in self._FILE_PATH_RE.finditer(text):
            raw = m.group()
            try:
                p = Path(raw).expanduser()
            except RuntimeError:
                p = Path(raw)
            if p.is_file() and p.suffix.lower() in self._SENDABLE_EXTENSIONS:
                found.append(p)
                spans_to_remove.append(m.span())

        if not spans_to_remove:
            return [], text

        parts = list(text)
        for start, end in reversed(spans_to_remove):
            parts[start:end] = []
        remaining = "".join(parts).strip()

        return found, remaining

    async def _upload_private_file(self, user_id: int, path: Path) -> None:
        """Upload a file to a private chat."""
        if not self._client:
            return
        try:
            await self._client.upload_private_file(
                user_id=str(user_id),
                file="file://" + str(path),
                name=path.name,
            )
            logger.info("Uploaded file to user %s: %s", user_id, path.name)
        except Exception as e:
            logger.warning("Failed to upload file %s to user %s: %s", path.name, user_id, e)

    # ── Main Loop ─────────────────────────────────────────────────

    async def run(self) -> None:
        """Main event loop: connect to daemon and NapCat, then process events."""
        # Connect to daemon
        if not await self.connect():
            console.print(
                "[red]无法连接到 VTuber daemon[/red]\n"
                "请先启动: [bold]vtuber start[/bold]"
            )
            return

        console.print("[green]已连接到 VTuber daemon[/green]")

        # Create NapCat client
        token = self._access_token or None
        self._client = NapCatClient(ws_url=self._ws_url, token=token)

        console.print(
            f"[green]正在连接 NapCat[/green] ({self._ws_url})\n"
            f"Owner ID: {self._owner_id or '(未设置)'}\n"
            f"等待消息中..."
        )

        # Start event loop
        self._event_task = asyncio.create_task(self._event_loop())

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
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
        for task in self._group_debounce_tasks.values():
            task.cancel()
        self._group_debounce_tasks.clear()
        await self.disconnect()
        console.print("[dim]NapCat provider 已停止[/dim]")


def main():
    """Entry point for the NapCat provider."""
    level = getattr(logging, get_config().log_level, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        provider = NapCatProvider()
        asyncio.run(provider.run())
    except KeyboardInterrupt:
        console.print("\n[dim]再见！[/dim]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[red]NapCat provider 错误：{e}[/red]")
        sys.exit(1)
