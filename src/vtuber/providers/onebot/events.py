"""OneBot v11 event handlers: message, notice, meta_event."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .message import extract_message_text

if TYPE_CHECKING:
    from .provider import OneBotProvider

logger = logging.getLogger("vtuber.provider.onebot.events")


async def handle_onebot_event(provider: OneBotProvider, event: dict) -> None:
    """Top-level dispatcher for incoming OneBot events.

    Also routes echo-based API responses back to waiting futures.
    """
    # Route API responses by echo field
    echo = event.get("echo")
    if echo and echo in provider._api_futures:
        future = provider._api_futures.pop(echo)
        if not future.done():
            future.set_result(event)
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

    sender_info = event.get("sender", {})
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

        # Track unseen message count
        provider._group_unseen[group_id] = provider._group_unseen.get(group_id, 0) + 1

        # Determine whether agent should reply
        should_reply = False

        # 1. Check if bot is @-mentioned
        if isinstance(message, list):
            for seg in message:
                if (
                    isinstance(seg, dict)
                    and seg.get("type") == "at"
                    and str(seg.get("data", {}).get("qq")) == str(provider._self_id)
                ):
                    should_reply = True
                    break

        # 2. Check if bot name is mentioned in text
        if not should_reply and provider._bot_names:
            text_lower = text.lower()
            for name in provider._bot_names:
                if name.lower() in text_lower:
                    should_reply = True
                    break

        # 3. Check if accumulated messages reached batch threshold
        if (
            not should_reply
            and provider._group_batch_size > 0
            and provider._group_unseen[group_id] >= provider._group_batch_size
        ):
            should_reply = True

        # Reset unseen counter when triggering reply
        if should_reply:
            provider._group_unseen[group_id] = 0

        session_id = f"onebot:group:{group_id}"

        # Register pending response only when expecting a reply
        if should_reply:
            provider._pending[session_id] = _PendingResponse(
                reply_to="group", group_id=group_id,
            )

        # Forward every group message to daemon for session recording
        await provider.send_message(
            text,
            sender=nickname,
            is_owner=is_owner,
            is_private=False,
            should_reply=should_reply,
            channel_id=str(group_id),
            session_id=session_id,
        )
        logger.debug(
            "Group msg from %s(%s) in %s (reply=%s): %s",
            nickname, user_id, group_id, should_reply, text[:50],
        )


# ── Notice events ──────────────────────────────────────────────────


async def _handle_notice_event(provider: OneBotProvider, event: dict) -> None:
    """Handle notice events (poke, etc.)."""
    from .provider import _PendingResponse

    notice_type = event.get("notice_type")
    sub_type = event.get("sub_type")

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
