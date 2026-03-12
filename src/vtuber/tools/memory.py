"""Memory tools — MCP tools for searching and reading conversation sessions."""

from typing import Any

from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

from vtuber.config import get_sessions_dir
from vtuber.session import SessionManager
from vtuber.tools._helpers import text_response


@tool(
    "search_sessions",
    "Search past memories by keyword. Use source='summary' (default) for quick recall from consolidated history, "
    "or source='detailed' when you need full conversation context.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keyword or phrase",
            },
            "source": {
                "type": "string",
                "enum": ["summary", "detailed"],
                "description": "summary = search consolidated history summaries (fast, recommended). "
                "detailed = search raw conversation logs (slower, full context).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 10)",
            },
        },
        "required": ["query"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def search_sessions(args: dict[str, Any]) -> dict[str, Any]:
    """Search past memories — summaries or raw conversations."""
    query = args["query"].lower()
    source = args.get("source", "summary")
    limit = args.get("limit", 10)

    if source == "summary":
        return _search_history(query, limit)
    return _search_sessions_detailed(query, limit)


def _search_history(query: str, limit: int) -> dict[str, Any]:
    """Search HISTORY.md paragraphs by keyword."""
    from vtuber.config import get_history_path

    history_path = get_history_path()
    if not history_path.exists():
        return text_response("No history found (HISTORY.md does not exist).")

    text = history_path.read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    results = []
    for para in paragraphs:
        if query in para.lower():
            results.append(para)
            if len(results) >= limit:
                break

    if not results:
        return text_response(f"No matches found for '{query}' in history summaries.")

    return text_response("\n\n---\n\n".join(results))


def _search_sessions_detailed(query: str, limit: int) -> dict[str, Any]:
    """Search raw session logs for matching messages with context."""
    manager = SessionManager(get_sessions_dir())
    results = []

    for session_info in manager.list_sessions():
        session = manager.get_or_create(session_info["key"])

        for i, entry in enumerate(session.messages):
            content = entry.get("content", "")
            if query not in content.lower():
                continue

            context_lines = []
            if i > 0:
                prev = session.messages[i - 1]
                context_lines.append(f"  [{prev['role']}] {prev['content'][:150]}")
            context_lines.append(f"  **[{entry['role']}] {content[:300]}**")
            if i + 1 < len(session.messages):
                nxt = session.messages[i + 1]
                context_lines.append(f"  [{nxt['role']}] {nxt['content'][:150]}")

            results.append(
                f"Session {session.key} ({entry.get('timestamp', '?')}):\n"
                + "\n".join(context_lines)
            )
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    if not results:
        return text_response(f"No matches found for '{query}' in session logs.")

    return text_response("\n\n---\n\n".join(results))


@tool(
    "list_sessions",
    "List recent conversation sessions with message counts and topic previews.",
    {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max sessions to list (default 10)",
            },
        },
        "required": [],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def list_sessions(args: dict[str, Any]) -> dict[str, Any]:
    """List recent conversation sessions with previews."""
    limit = args.get("limit", 10)

    manager = SessionManager(get_sessions_dir())
    sessions = manager.list_sessions()[:limit]

    if not sessions:
        return text_response("No session logs found.")

    lines = ["Recent sessions:\n"]
    for session_info in sessions:
        session = manager.get_or_create(session_info["key"])
        user_count = sum(1 for m in session.messages if m.get("role") == "user")

        topics = []
        for m in session.messages:
            if m.get("role") == "user":
                text = m.get("content", "").replace("\n", " ").strip()
                if text:
                    topics.append(text[:100])
                if len(topics) >= 3:
                    break
        preview = " / ".join(topics) if topics else "(empty)"

        lines.append(f"- **{session.key}** ({len(session.messages)} msgs, {user_count} from user): {preview}")

    return text_response("\n".join(lines))


@tool(
    "read_session",
    "Read the full content of a specific past conversation session.",
    {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session ID (e.g., 'cli:main', 'discord:user_123')",
            },
        },
        "required": ["session_id"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def read_session(args: dict[str, Any]) -> dict[str, Any]:
    """Read a specific session's full conversation."""
    session_id = args["session_id"]

    manager = SessionManager(get_sessions_dir())
    session = manager.get_or_create(session_id)

    if not session.messages:
        return text_response(f"Session '{session_id}' is empty or not found.")

    lines = [f"Session: {session_id} ({len(session.messages)} messages)\n"]
    for entry in session.messages:
        ts = entry.get("timestamp", "?")
        role = entry.get("role", "?")
        content = entry.get("content", "")
        lines.append(f"[{ts}] **{role}**: {content}")
    return text_response("\n\n".join(lines))
