"""Long text → image rendering via external Text2Image service."""

import logging

import httpx

logger = logging.getLogger("vtuber.provider.onebot.render")

# ── Styled HTML template ────────────────────────────────────────────
_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}}
html, body {{
    background: transparent;
}}
.container {{
    font-family: -apple-system, "Noto Sans SC", "PingFang SC",
                 "Microsoft YaHei", sans-serif;
    font-size: 15px;
    line-height: 1.7;
    color: #1a1a2e;
    background: #ffffff;
    padding: 28px 32px;
    border-radius: 12px;
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}}
p {{
    margin-bottom: 0.6em;
}}
p:last-child {{
    margin-bottom: 0;
}}
h1, h2, h3, h4, h5, h6 {{
    margin-top: 0.8em;
    margin-bottom: 0.4em;
    font-weight: 600;
    color: #16213e;
}}
h1 {{ font-size: 1.5em; }}
h2 {{ font-size: 1.3em; }}
h3 {{ font-size: 1.15em; }}
strong {{
    font-weight: 600;
    color: #16213e;
}}
em {{
    font-style: italic;
}}
a {{
    color: #4361ee;
    text-decoration: none;
}}
code {{
    font-family: "JetBrains Mono", "Fira Code", "SF Mono", Menlo,
                 Consolas, monospace;
    font-size: 0.88em;
    background: #f0f1f5;
    color: #e74c3c;
    padding: 2px 6px;
    border-radius: 4px;
}}
pre {{
    margin: 0.8em 0;
    padding: 16px 20px;
    background: #282c34;
    color: #abb2bf;
    border-radius: 8px;
    overflow-x: auto;
    line-height: 1.5;
}}
pre code {{
    background: none;
    color: inherit;
    padding: 0;
    font-size: 0.85em;
}}
blockquote {{
    margin: 0.6em 0;
    padding: 8px 16px;
    border-left: 3px solid #4361ee;
    background: #f8f9fc;
    color: #555;
    border-radius: 0 6px 6px 0;
}}
ul, ol {{
    margin: 0.4em 0 0.6em 1.5em;
}}
li {{
    margin-bottom: 0.25em;
}}
table {{
    border-collapse: collapse;
    margin: 0.8em 0;
    width: 100%;
}}
th, td {{
    border: 1px solid #e0e0e0;
    padding: 8px 12px;
    text-align: left;
}}
th {{
    background: #f0f1f5;
    font-weight: 600;
}}
tr:nth-child(even) {{
    background: #fafbfd;
}}
hr {{
    border: none;
    border-top: 1px solid #e0e0e0;
    margin: 1em 0;
}}
</style>
</head>
<body>
<div class="container">
{content}
</div>
</body>
</html>
"""

_VIEWPORT_WIDTH = 520


def should_render_as_image(text: str, *, threshold: int, enabled: bool) -> bool:
    """Check if *text* should be rendered as an image instead of plain text."""
    if not enabled:
        return False
    if len(text) >= threshold:
        return True
    if "```" in text:
        return True
    return False


def _md_to_styled_html(text: str) -> str:
    """Convert markdown text to a fully-styled HTML document."""
    try:
        import markdown

        content = markdown.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br"],
        )
    except ImportError:
        import html as html_module

        content = f"<pre>{html_module.escape(text)}</pre>"

    return _HTML_TEMPLATE.format(content=content)


async def render_text_as_image(text: str, text2img_url: str) -> str | None:
    """Render markdown *text* to an image via the Text2Image service.

    Returns the image URL on success, or ``None`` on failure (caller should
    fall back to plain text).
    """
    html = _md_to_styled_html(text)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{text2img_url}/generate",
                json={
                    "html": html,
                    "json": True,
                    "options": {
                        "viewport_width": _VIEWPORT_WIDTH,
                        "full_page": True,
                        "omit_background": True,
                        "type": "png",
                        "device_scale_factor_level": "high",
                    },
                },
            )
            resp.raise_for_status()
            body = resp.json()
            image_id = body.get("data", {}).get("id")
            if image_id:
                return f"{text2img_url}/{image_id}"
    except Exception as e:
        logger.warning("Text2Image service failed: %s", e)
    return None
