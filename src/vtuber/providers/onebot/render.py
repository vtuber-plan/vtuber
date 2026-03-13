"""Long text → image rendering via external Text2Image service."""

import logging

import httpx

logger = logging.getLogger("vtuber.provider.onebot.render")


def should_render_as_image(text: str, *, threshold: int, enabled: bool) -> bool:
    """Check if *text* should be rendered as an image instead of plain text."""
    if not enabled:
        return False
    if len(text) >= threshold:
        return True
    if "```" in text:
        return True
    return False


async def render_text_as_image(text: str, text2img_url: str) -> str | None:
    """Render markdown *text* to an image via the Text2Image service.

    Returns the image URL on success, or ``None`` on failure (caller should
    fall back to plain text).
    """
    try:
        import markdown

        html = markdown.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br"],
        )
    except ImportError:
        import html as html_module

        html = f"<pre>{html_module.escape(text)}</pre>"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{text2img_url}/generate",
                json={"html": html, "json": True},
            )
            resp.raise_for_status()
            body = resp.json()
            image_id = body.get("data", {}).get("id")
            if image_id:
                return f"{text2img_url}/{image_id}"
    except Exception as e:
        logger.warning("Text2Image service failed: %s", e)
    return None
