"""Web search and fetch tools."""

import logging
from typing import Any

import httpx
from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

from vtuber.config import get_config

logger = logging.getLogger(__name__)

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_DEFAULT_READ_LENGTH = 10000
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@tool(
    "web_search",
    "Search the web using Tavily. Returns structured results with titles, URLs, and snippets.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Max number of results (default 5, max 10)",
            },
        },
        "required": ["query"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def web_search(args: dict[str, Any]) -> dict[str, Any]:
    """Search the web via Tavily API."""
    api_key = get_config().tavily_api_key
    if not api_key:
        return _text(
            "Error: tavily_api_key is not configured. "
            "Add it to ~/.vtuber/config.yaml (get one at https://tavily.com)"
        )

    query = args["query"]
    max_results = min(max(args.get("max_results", 5), 1), 10)

    try:
        async with httpx.AsyncClient(timeout=get_config().web_timeout) as client:
            resp = await client.post(
                _TAVILY_SEARCH_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        return _text(f"Search API error: HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        return _text(f"Search request failed: {e}")
    except Exception as e:
        return _text(f"Search error: {e}")

    results = data.get("results", [])
    if not results:
        return _text(f"No results found for '{query}'.")

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        snippet = r.get("content", "").strip()
        lines.append(f"{i}. **{title}**\n   URL: {url}\n   {snippet}")

    return _text("\n\n".join(lines))


@tool(
    "web_fetch",
    (
        "Fetch a web page and extract its main text content. Returns clean text without HTML. "
        "Use 'offset' and 'limit' to read specific portions of long pages (like the Read tool). "
        "The response includes total_length so you can request remaining content if needed."
    ),
    {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch",
            },
            "offset": {
                "type": "integer",
                "description": "Character offset to start reading from (default 0)",
            },
            "limit": {
                "type": "integer",
                "description": f"Max characters to return (default {_DEFAULT_READ_LENGTH})",
            },
        },
        "required": ["url"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def web_fetch(args: dict[str, Any]) -> dict[str, Any]:
    """Fetch a web page and extract main text content with pagination support."""
    url = args["url"]
    offset = max(args.get("offset", 0) or 0, 0)
    limit = max(args.get("limit", _DEFAULT_READ_LENGTH) or _DEFAULT_READ_LENGTH, 1)

    if not url.startswith(("http://", "https://")):
        return _text("Error: URL must start with http:// or https://")

    try:
        async with httpx.AsyncClient(
            timeout=get_config().web_timeout,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return _text(f"Fetch error: HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        return _text(f"Fetch request failed: {e}")
    except Exception as e:
        return _text(f"Fetch error: {e}")

    content_type = resp.headers.get("content-type", "")
    if not any(t in content_type for t in ("text/html", "text/plain", "application/json")):
        return _text(f"Unsupported content type: {content_type}")

    html = resp.text

    # Extract main text using trafilatura
    import trafilatura

    text = trafilatura.extract(html, include_links=True, include_tables=True)

    if not text:
        text = html
        extraction_note = "[Trafilatura extraction failed, showing raw content]\n\n"
    else:
        extraction_note = ""

    total_length = len(text)
    chunk = text[offset:offset + limit]

    header = f"URL: {url}\n"
    header += f"Total length: {total_length} chars | Showing: {offset}-{offset + len(chunk)}"
    if offset + len(chunk) < total_length:
        remaining = total_length - offset - len(chunk)
        header += f" | {remaining} chars remaining (use offset={offset + len(chunk)} to continue)"
    header += "\n\n"

    return _text(f"{header}{extraction_note}{chunk}")


def _text(text: str) -> dict[str, Any]:
    """Helper to build a text content response."""
    return {"content": [{"type": "text", "text": text}]}
