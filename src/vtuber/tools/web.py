"""Web search and fetch tools."""

import logging
from typing import Any

import httpx
from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

from vtuber.config import get_config

logger = logging.getLogger(__name__)

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_DEFAULT_TIMEOUT = 30.0
_MAX_FETCH_LENGTH = 20000
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
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
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
    "Fetch a web page and extract its main text content. Returns clean text without HTML.",
    {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch",
            },
        },
        "required": ["url"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def web_fetch(args: dict[str, Any]) -> dict[str, Any]:
    """Fetch a web page and extract main text content."""
    url = args["url"]

    if not url.startswith(("http://", "https://")):
        return _text("Error: URL must start with http:// or https://")

    try:
        async with httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
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
        # Fallback: return raw text, stripped of excessive whitespace
        text = html[:_MAX_FETCH_LENGTH]
        text = f"[Trafilatura extraction failed, showing raw content]\n\n{text}"

    # Truncate if needed
    if len(text) > _MAX_FETCH_LENGTH:
        text = text[:_MAX_FETCH_LENGTH] + f"\n\n[Content truncated at {_MAX_FETCH_LENGTH} characters]"

    return _text(f"URL: {url}\n\n{text}")


def _text(text: str) -> dict[str, Any]:
    """Helper to build a text content response."""
    return {"content": [{"type": "text", "text": text}]}
