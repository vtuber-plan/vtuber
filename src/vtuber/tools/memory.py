"""Memory system — short-term session logs + long-term persistent memory."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

from vtuber.config import get_sessions_dir, get_long_term_memory_path, ensure_sessions_dir


# --- Helper functions (called by daemon, not tools) ---

def create_session_id() -> str:
    """Create a timestamp-based session ID."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def log_message(session_id: str, role: str, content: str) -> None:
    """Append a message to the session log file."""
    sessions_dir = ensure_sessions_dir()
    log_file = sessions_dir / f"{session_id}.jsonl"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "content": content,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --- Tools for the AI ---

@tool(
    "search_sessions",
    "Search through past conversation session logs by keyword. Returns matching messages with timestamps and context.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keyword or phrase to look for in conversation history",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 10)",
            },
        },
        "required": ["query"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def search_sessions(args: dict[str, Any]) -> dict[str, Any]:
    """Search through session logs for matching messages."""
    query = args["query"].lower()
    limit = args.get("limit", 10)
    sessions_dir = get_sessions_dir()

    if not sessions_dir.exists():
        return {"content": [{"type": "text", "text": "No session logs found."}]}

    results = []
    # Sort session files by name (newest first since names are timestamps)
    session_files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)

    for session_file in session_files:
        session_name = session_file.stem
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if query in entry.get("content", "").lower():
                        results.append(
                            f"[{session_name}] {entry['timestamp']} ({entry['role']}): {entry['content'][:200]}"
                        )
                        if len(results) >= limit:
                            break
        except (json.JSONDecodeError, KeyError):
            continue
        if len(results) >= limit:
            break

    if not results:
        return {"content": [{"type": "text", "text": f"No matches found for '{args['query']}'."}]}

    return {"content": [{"type": "text", "text": "\n\n".join(results)}]}


@tool(
    "list_sessions",
    "List recent conversation sessions with dates and first message preview.",
    {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of sessions to list (default: 10)",
            },
        },
        "required": [],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def list_sessions(args: dict[str, Any]) -> dict[str, Any]:
    """List recent conversation sessions."""
    limit = args.get("limit", 10)
    sessions_dir = get_sessions_dir()

    if not sessions_dir.exists():
        return {"content": [{"type": "text", "text": "No session logs found."}]}

    session_files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)[:limit]

    if not session_files:
        return {"content": [{"type": "text", "text": "No session logs found."}]}

    lines = ["Recent sessions:"]
    for session_file in session_files:
        session_name = session_file.stem
        first_user_msg = ""
        msg_count = 0
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    msg_count += 1
                    entry = json.loads(line)
                    if not first_user_msg and entry.get("role") == "user":
                        first_user_msg = entry.get("content", "")[:80]
        except (json.JSONDecodeError, KeyError):
            continue
        preview = first_user_msg if first_user_msg else "(empty)"
        lines.append(f"- {session_name} ({msg_count} messages): {preview}")

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "update_long_term_memory",
    "Append an insight or important note to long-term memory. Use sparingly — only for consolidated, important patterns and facts worth remembering across all future sessions. This is NOT for recording conversations.",
    {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The insight or note to remember permanently. Should be concise and self-contained.",
            },
        },
        "required": ["content"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def update_long_term_memory(args: dict[str, Any]) -> dict[str, Any]:
    """Append to long-term memory file."""
    content = args["content"]
    memory_path = get_long_term_memory_path()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## {timestamp}\n\n{content}\n"

    # Create file with header if it doesn't exist
    if not memory_path.exists():
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        memory_path.write_text(
            "# Long-term Memory\n\nThis file contains consolidated insights and important notes.\n" + entry,
            encoding="utf-8",
        )
    else:
        with open(memory_path, "a", encoding="utf-8") as f:
            f.write(entry)

    return {
        "content": [{"type": "text", "text": f"Long-term memory updated: {content[:100]}..."}]
    }
