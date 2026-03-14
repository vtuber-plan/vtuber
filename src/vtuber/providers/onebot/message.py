"""Message parsing: text extraction, reply/forward resolution, file download."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from vtuber.config import get_workspace_dir

if TYPE_CHECKING:
    from .provider import OneBotProvider

logger = logging.getLogger("vtuber.provider.onebot.message")


# ── Synchronous helpers (no I/O) ───────────────────────────────────


def extract_text(message) -> str:
    """Extract plain text from a OneBot message (string or segment array).

    This is a fast, synchronous helper used for group context ring-buffers
    where we do not want to await API calls.
    """
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
        return "".join(parts)
    return str(message)


# ── Async full extraction ──────────────────────────────────────────


async def extract_message_text(
    provider: OneBotProvider,
    message,
    *,
    is_private: bool = False,
) -> str:
    """Extract text from message segments, resolving replies, forwards, and files."""
    if isinstance(message, str):
        return message
    if not isinstance(message, list):
        return str(message)

    parts: list[str] = []
    for seg in message:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type")
        data = seg.get("data", {})

        if seg_type == "text":
            parts.append(data.get("text", ""))

        elif seg_type == "reply":
            msg_id = data.get("id")
            if msg_id:
                ctx = await _fetch_reply_context(provider, msg_id)
                if ctx:
                    parts.insert(0, ctx)

        elif seg_type == "forward":
            fwd_id = data.get("id")
            if fwd_id:
                ctx = await _fetch_forward_context(provider, fwd_id)
                if ctx:
                    parts.append(ctx)

        elif seg_type in ("file", "record") and is_private:
            url = data.get("url", "")
            filename = data.get("file", "")
            file_id = data.get("file_id", "")

            # If no direct URL, try get_file API with file_id
            if not url and file_id:
                url = await _resolve_file_url(provider, file_id)

            if url:
                local_path = await download_file(url, filename)
                if local_path:
                    label = {"file": "文件", "record": "语音"}.get(seg_type, "文件")
                    parts.append(f"[{label}: {local_path}]")
                else:
                    parts.append(f"[{seg_type}: 下载失败]")
            elif filename:
                parts.append(f"[{seg_type}: {filename} (无法获取下载链接)]")

        elif seg_type == "at":
            pass  # handled elsewhere for trigger detection

    return "".join(parts)


# ── Reply context ──────────────────────────────────────────────────


async def _fetch_reply_context(provider: OneBotProvider, message_id: str) -> str | None:
    """Fetch the original message for a reply segment via ``get_msg``."""
    resp = await provider.send_onebot_action(
        "get_msg", {"message_id": int(message_id)}, wait=True, timeout=5.0,
    )
    if not resp or resp.get("status") != "ok":
        return None
    msg_data = resp.get("data", {})
    sender = msg_data.get("sender", {}).get("nickname", "未知")
    raw_msg = msg_data.get("message", "")
    original_text = extract_text(raw_msg).strip()
    if not original_text:
        return None
    if len(original_text) > 100:
        original_text = original_text[:100] + "..."
    return f"[回复 {sender}: {original_text}]\n"


# ── Forward context ────────────────────────────────────────────────


async def _fetch_forward_context(provider: OneBotProvider, forward_id: str) -> str | None:
    """Fetch all messages in a forward/merge segment via ``get_forward_msg``."""
    resp = await provider.send_onebot_action(
        "get_forward_msg", {"id": forward_id}, wait=True, timeout=10.0,
    )
    if not resp or resp.get("status") != "ok":
        return None
    data = resp.get("data", {})
    messages = data.get("messages") or data.get("message", [])
    if not messages:
        return None

    lines = ["[合并转发]"]
    for node in messages[:20]:  # cap to avoid huge payloads
        node_data = node.get("data", node)  # handle both segment and flat formats
        sender = (
            node_data.get("sender", {}).get("nickname")
            or node_data.get("nickname")
            or node_data.get("name")
            or "未知"
        )
        content = extract_text(node_data.get("content", "")).strip()
        if content:
            lines.append(f"{sender}: {content}")
    return "\n".join(lines) + "\n"


# ── File URL resolution ────────────────────────────────────────────


async def _resolve_file_url(provider: OneBotProvider, file_id: str) -> str:
    """Resolve a file_id to a download URL via the ``get_file`` API."""
    resp = await provider.send_onebot_action(
        "get_file", {"file_id": file_id}, wait=True, timeout=10.0,
    )
    if resp and resp.get("status") == "ok":
        data = resp.get("data", {})
        url = data.get("url", "") or data.get("base64", "")
        if url:
            logger.debug("Resolved file_id=%s → url=%s", file_id, url[:80])
            return url
    logger.warning("Failed to resolve file_id=%s: %s", file_id, resp)
    return ""


# ── File download ──────────────────────────────────────────────────


async def download_file(url: str, original_name: str = "") -> Path | None:
    """Download a file from *url* to the workspace ``Downloads`` directory."""
    downloads_dir = get_workspace_dir() / "Downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    if not original_name:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        original_name = Path(parsed.path).name or "unknown"

    # Strip query-string artifacts from filename
    if "?" in original_name:
        original_name = original_name.split("?")[0]

    target = downloads_dir / original_name
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        target = downloads_dir / f"{stem}_{int(time.time())}{suffix}"

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            target.write_bytes(resp.content)
        logger.info("Downloaded file to %s", target)
        return target
    except Exception as e:
        logger.error("Failed to download %s: %s", url, e)
        return None
