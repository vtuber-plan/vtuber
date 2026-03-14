"""OneBot v11 event handlers: message, notice, meta_event."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .message import _resolve_file_url, download_file, extract_message_text

if TYPE_CHECKING:
    from .provider import OneBotProvider

logger = logging.getLogger("vtuber.provider.onebot.events")


async def handle_onebot_event(provider: OneBotProvider, event: dict) -> None:
    """Top-level dispatcher for incoming OneBot events.

    Also routes echo-based API responses back to waiting futures.
    """
    # Route API responses by echo field (normalize to str — some impls return int)
    echo = event.get("echo")
    if echo is not None:
        echo = str(echo)
    if echo and echo in provider._api_futures:
        future = provider._api_futures.pop(echo)
        if not future.done():
            try:
                future.set_result(event)
            except asyncio.InvalidStateError:
                pass
        return

    post_type = event.get("post_type")
    if post_type == "meta_event":
        await _handle_meta_event(provider, event)
    elif post_type == "message":
        await _handle_message_event(provider, event)
    elif post_type == "notice":
        await _handle_notice_event(provider, event)


# ── Meta events ────────────────────────────────────────────────────


async def _handle_meta_event(provider: OneBotProvider, event: dict) -> None:
    """Handle meta events (lifecycle, heartbeat)."""
    meta_type = event.get("meta_event_type")
    if meta_type == "lifecycle":
        provider._self_id = event.get("self_id")
        sub = event.get("sub_type", "")
        logger.info("OneBot lifecycle: %s (self_id=%s)", sub, provider._self_id)


# ── Message events ─────────────────────────────────────────────────


async def _handle_message_event(provider: OneBotProvider, event: dict) -> None:
    """Handle incoming message events (private & group)."""
    from .provider import _PendingResponse

    message_type = event.get("message_type")
    user_id = event.get("user_id")
    raw_message = event.get("raw_message", "")
    message = event.get("message", raw_message)

    is_private = message_type == "private"

    # Full async extraction (resolves reply, forward, files)
    text = (await extract_message_text(provider, message, is_private=is_private)).strip()

    if not text or user_id == provider._self_id:
        return

    # Whitelist filtering — owner always passes
    is_owner = provider.owner_id and str(user_id) == provider.owner_id

    if message_type == "private":
        if (
            not is_owner
            and provider._user_whitelist
            and str(user_id) not in provider._user_whitelist
        ):
            return
    elif message_type == "group":
        group_id = event.get("group_id")
        if provider._group_whitelist and str(group_id) not in provider._group_whitelist:
            return
    else:
        return

    sender_info = event.get("sender") or {}
    nickname = (
        sender_info.get("card")
        or sender_info.get("nickname")
        or str(user_id)
    )

    if message_type == "private":
        session_id = f"onebot:private:{user_id}"
        provider._pending[session_id] = _PendingResponse(
            reply_to="private", user_id=user_id,
        )
        await provider.send_message(
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

        session_id = f"onebot:group:{group_id}"

        # Determine if this is a mention (@ or bot name)
        is_mention = _check_mention(provider, message, text)

        if is_mention:
            # Cancel any active debounce timer — mention takes priority
            _cancel_debounce(provider, group_id)

            provider._pending[session_id] = _PendingResponse(
                reply_to="group", group_id=group_id,
            )
            await provider.send_message(
                text,
                sender=nickname,
                is_owner=is_owner,
                is_private=False,
                should_reply=True,
                channel_id=str(group_id),
                session_id=session_id,
            )
        else:
            # Non-mention: record in daemon, start/reset debounce timer
            await provider.send_message(
                text,
                sender=nickname,
                is_owner=is_owner,
                is_private=False,
                should_reply=False,
                channel_id=str(group_id),
                session_id=session_id,
            )
            if provider._group_reply_delay > 0:
                _start_debounce(provider, group_id, session_id)

        logger.debug(
            "Group msg from %s(%s) in %s (mention=%s): %s",
            nickname, user_id, group_id, is_mention, text[:50],
        )


# ── Group chat helpers ────────────────────────────────────────────


def _check_mention(provider: OneBotProvider, message: object, text: str) -> bool:
    """Check if the bot is @-mentioned or its name appears in the text."""
    # 1. Check @mention in message segments
    if isinstance(message, list):
        for seg in message:
            if (
                isinstance(seg, dict)
                and seg.get("type") == "at"
                and str(seg.get("data", {}).get("qq")) == str(provider._self_id)
            ):
                return True

    # 2. Check bot name in text
    if provider._bot_names:
        text_lower = text.lower()
        for name in provider._bot_names:
            if name.lower() in text_lower:
                return True

    return False


def _cancel_debounce(provider: OneBotProvider, group_id: int) -> None:
    """Cancel an active debounce timer for a group, if any."""
    task = provider._group_debounce_tasks.pop(group_id, None)
    if task and not task.done():
        task.cancel()


def _start_debounce(
    provider: OneBotProvider, group_id: int, session_id: str,
) -> None:
    """Start or reset the debounce timer for a group."""
    _cancel_debounce(provider, group_id)
    provider._group_debounce_tasks[group_id] = asyncio.create_task(
        _debounce_flush(provider, group_id, session_id)
    )


async def _debounce_flush(
    provider: OneBotProvider, group_id: int, session_id: str,
) -> None:
    """Wait for the debounce delay, then signal the daemon to evaluate context."""
    from .provider import _PendingResponse

    try:
        await asyncio.sleep(provider._group_reply_delay)
    except asyncio.CancelledError:
        return

    # Timer fired — clean up our reference
    provider._group_debounce_tasks.pop(group_id, None)

    # Skip if agent is already processing a reply for this group
    if session_id in provider._pending:
        logger.debug("Debounce flush skipped for group %s — pending response", group_id)
        return

    # Register pending response (agent might reply)
    provider._pending[session_id] = _PendingResponse(
        reply_to="group", group_id=group_id,
    )

    # Send empty-content flush: daemon skips recording, goes straight to agent
    await provider.send_message(
        "",
        sender="",
        is_owner=False,
        is_private=False,
        should_reply=True,
        channel_id=str(group_id),
        session_id=session_id,
    )
    logger.debug("Debounce flush for group %s", group_id)


# ── File upload helpers ────────────────────────────────────────────


async def _handle_file_upload(
    provider: OneBotProvider, event: dict,
) -> None:
    """Handle private file upload notice (offline_file)."""
    from .provider import _PendingResponse

    user_id = event.get("user_id")
    is_owner = provider.owner_id and str(user_id) == provider.owner_id
    nickname = str(user_id)

    if (
        not is_owner
        and provider._user_whitelist
        and str(user_id) not in provider._user_whitelist
    ):
        return

    file_info = event.get("file", {})
    url = file_info.get("url", "")
    filename = file_info.get("name", "")
    file_id = file_info.get("id", "") or file_info.get("file_id", "")

    # If no direct URL, try get_file API with file_id
    if not url and file_id:
        url = await _resolve_file_url(provider, file_id)

    if not url:
        logger.warning("File upload notice without URL (no file_id either): %s", event)
        return

    local_path = await download_file(url, filename)
    if not local_path:
        logger.warning("Failed to download uploaded file: %s", filename)
        return

    synthetic_text = f"[文件: {local_path}]"
    session_id = f"onebot:private:{user_id}"
    provider._pending[session_id] = _PendingResponse(
        reply_to="private", user_id=user_id,
    )
    await provider.send_message(
        synthetic_text,
        sender=nickname,
        is_owner=is_owner,
        is_private=True,
        session_id=session_id,
    )

    logger.info("File upload from %s: %s → %s", nickname, filename, local_path)


# ── Notice events ──────────────────────────────────────────────────


async def _handle_notice_event(provider: OneBotProvider, event: dict) -> None:
    """Handle notice events (poke, file uploads, etc.)."""
    from .provider import _PendingResponse

    notice_type = event.get("notice_type")
    sub_type = event.get("sub_type")

    # ── Private file upload notice ────────────────────────────────
    if notice_type == "offline_file":
        await _handle_file_upload(provider, event)
        return

    if notice_type == "notify" and sub_type == "poke":
        target_id = event.get("target_id")
        if target_id != provider._self_id:
            return  # not poked at us

        user_id = event.get("user_id")
        group_id = event.get("group_id")
        nickname = str(user_id)

        synthetic_text = f"[戳一戳] {nickname} 戳了戳你"
        is_owner = provider.owner_id and str(user_id) == provider.owner_id

        if group_id:
            if provider._group_whitelist and str(group_id) not in provider._group_whitelist:
                return
            session_id = f"onebot:group:{group_id}"
            provider._pending[session_id] = _PendingResponse(
                reply_to="group", group_id=group_id,
            )
            await provider.send_message(
                synthetic_text,
                sender=nickname,
                is_owner=is_owner,
                is_private=False,
                channel_id=str(group_id),
                session_id=session_id,
            )
        else:
            session_id = f"onebot:private:{user_id}"
            provider._pending[session_id] = _PendingResponse(
                reply_to="private", user_id=user_id,
            )
            await provider.send_message(
                synthetic_text,
                sender=nickname,
                is_owner=is_owner,
                is_private=True,
                session_id=session_id,
            )
