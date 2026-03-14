"""NapCat provider — connects to NapCat via napcat-sdk typed client."""

import asyncio
import base64
import hashlib
import logging
import uuid
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from napcat import (
    At,
    File,
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
from vtuber.providers.files import parse_file_reply
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
                # napcat-sdk has no OfflineFileEvent; QQ private file transfers
                # arrive as notice_type="offline_file" and fall through to
                # UnknownNoticeEvent.  Handle them from _raw.
                raw = event._raw
                if raw.get("notice_type") == "offline_file":
                    await self._handle_offline_file(raw)


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

        # Extract file info from raw event data
        raw = event._raw
        file_info = raw.get("file", {})
        url = file_info.get("url", "")
        filename = file_info.get("name", "")
        file_id = file_info.get("id", "") or file_info.get("file_id", "")

        if not url and file_id:
            url = await self._resolve_file_url(file_id)

        if not url:
            logger.warning("File upload notice without downloadable URL: file_id=%s, raw=%s", file_id, raw)
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

    async def _handle_offline_file(self, raw: dict) -> None:
        """Handle offline_file notice (private file transfer, no typed SDK event)."""
        from vtuber.providers.onebot.message import download_file

        user_id = raw.get("user_id")
        if not user_id:
            return

        is_owner = self._owner_id and str(user_id) == self._owner_id
        if not is_owner and self._user_whitelist and str(user_id) not in self._user_whitelist:
            return

        file_info = raw.get("file", {})
        url = file_info.get("url", "")
        filename = file_info.get("name", "")
        file_id = file_info.get("id", "") or file_info.get("file_id", "")

        if not url and file_id:
            url = await self._resolve_file_url(file_id)

        if not url:
            logger.warning("offline_file notice without downloadable URL: file_id=%s, raw=%s", file_id, raw)
            return

        local_path = await download_file(url, filename)
        if not local_path:
            logger.warning("Failed to download offline file: %s", filename)
            return

        nickname = str(user_id)
        session_id = f"napcat:private:{user_id}"
        self._pending[session_id] = _PendingResponse(reply_to="private", user_id=int(user_id))
        await self.send_message(
            f"[文件: {local_path}]",
            sender=nickname,
            is_owner=is_owner,
            is_private=True,
            session_id=session_id,
        )
        logger.info("Offline file from %s: %s -> %s", nickname, filename, local_path)

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

                case Record(file=file_field) if is_private:
                    url = getattr(seg, "url", "") or ""
                    if not url and file_field:
                        url = await self._resolve_file_url(file_field)
                    if url:
                        local_path = await download_file(url, getattr(seg, "name", "") or file_field or "")
                        if local_path:
                            parts.append(f"[语音: {local_path}]")
                        else:
                            parts.append("[语音: 下载失败]")
                    else:
                        parts.append(f"[语音: {file_field} (无法获取下载链接)]")

                case File(file=file_field) if is_private:
                    url = getattr(seg, "url", "") or ""
                    if not url and file_field:
                        url = await self._resolve_file_url(file_field)
                    if url:
                        local_path = await download_file(url, getattr(seg, "name", "") or file_field or "")
                        if local_path:
                            parts.append(f"[文件: {local_path}]")
                        else:
                            parts.append("[文件: 下载失败]")
                    else:
                        parts.append(f"[文件: {file_field} (无法获取下载链接)]")

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

    # ── File URL Resolution ────────────────────────────────────────

    async def _resolve_file_url(self, file_id: str) -> str:
        """Resolve a file_id to a download URL via NapCat API.

        Tries ``get_private_file_url`` first, falls back to ``get_file``.
        """
        if not self._client:
            return ""

        # Try get_private_file_url first
        try:
            resp = await self._client.get_private_file_url(file_id=file_id)
            url = ""
            if isinstance(resp, dict):
                url = resp.get("url", "") or resp.get("private_url", "")
            else:
                url = getattr(resp, "url", "") or getattr(resp, "private_url", "")
            if url:
                logger.info("Resolved file_id=%s via get_private_file_url", file_id)
                return url
        except Exception as e:
            logger.debug("get_private_file_url failed for %s: %s", file_id, e)

        # Fallback: get_file
        try:
            resp = await self._client.get_file(file_id=file_id)
            url = ""
            if isinstance(resp, dict):
                url = resp.get("url", "")
            else:
                url = getattr(resp, "url", "")
            if url:
                logger.info("Resolved file_id=%s via get_file", file_id)
                return url
        except Exception as e:
            logger.debug("get_file failed for %s: %s", file_id, e)

        logger.warning("Failed to resolve file_id=%s — no download URL obtained", file_id)
        return ""

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

    async def _send_reply(self, pending: _PendingResponse, text: str) -> None:
        """Send a reply, rendering as image if needed."""
        if not self._client:
            return

        # In private chat, if the entire reply is a JSON array of absolute paths,
        # send them as file uploads instead of text.
        if pending.reply_to == "private" and pending.user_id:
            file_paths = parse_file_reply(text)
            if file_paths:
                for fp in file_paths:
                    await self._upload_private_file(pending.user_id, fp)
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

    async def _upload_private_file(self, user_id: int, path: Path) -> None:
        """Upload a file to a private chat via streaming upload.

        Reads the file locally, uploads it in base64 chunks via
        ``upload_file_stream``, then sends the resulting server-side path
        via ``upload_private_file``.
        """
        if not self._client:
            return

        CHUNK_SIZE = 512 * 1024  # 512 KB per chunk

        try:
            data = path.read_bytes()
            file_size = len(data)
            sha256 = hashlib.sha256(data).hexdigest()
            stream_id = uuid.uuid4().hex

            # Split into base64-encoded chunks
            total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
            if total_chunks == 0:
                total_chunks = 1

            for i in range(total_chunks):
                chunk = data[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]
                b64_chunk = base64.b64encode(chunk).decode("ascii")
                await self._client.upload_file_stream(
                    stream_id=stream_id,
                    chunk_data=b64_chunk,
                    chunk_index=i,
                    total_chunks=total_chunks,
                    file_size=file_size,
                    expected_sha256=sha256,
                    filename=path.name,
                )

            # Signal completion and get server-side file path
            resp = await self._client.upload_file_stream(
                stream_id=stream_id,
                is_complete=True,
                file_size=file_size,
                expected_sha256=sha256,
                filename=path.name,
            )

            # Extract server-side path from response
            server_path = ""
            if isinstance(resp, dict):
                server_path = resp.get("file_path", "") or resp.get("path", "")

            if server_path:
                await self._client.upload_private_file(
                    user_id=str(user_id),
                    file=server_path,
                    name=path.name,
                )
            else:
                logger.warning(
                    "Stream upload returned no server path for %s: %s",
                    path.name, resp,
                )
                return

            logger.info("Uploaded file to user %s: %s", user_id, path.name)
            try:
                await self._client.clean_stream_temp_file()
            except Exception as e:
                logger.debug("Failed to clean stream temp file: %s", e)
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
