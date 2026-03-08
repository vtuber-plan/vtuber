"""Memory system — short-term session logs + long-term persistent memory."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

from vtuber.config import get_sessions_dir, ensure_sessions_dir, get_history_path
import logging

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy persistence.
    Messages are append-only for LLM cache efficiency.
    """

    key: str  # channel:chat_id (e.g., "cli:main", "discord:user_123")
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = _safe_filename(key)
        return self.sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """Get an existing session or create a new one."""
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.error(f"Failed to load session {key}: {e}")
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._cache[session.key] = session

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions."""
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)


# --- Helper functions (called by daemon, not tools) ---


def _safe_filename(key: str) -> str:
    """Convert session key to safe filename."""
    return key.replace(":", "_").replace("/", "_")


def create_session_id() -> str:
    """Create a timestamp-based session ID."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def log_message(session_id: str, role: str, content: str, sender: str | None = None) -> None:
    """Append a message to the session log file."""
    sessions_dir = ensure_sessions_dir()
    log_file = sessions_dir / f"{session_id}.jsonl"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "content": content,
    }
    if sender and sender != "owner":
        entry["sender"] = sender
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _parse_session_file(path: Path) -> list[dict]:
    """Parse a session JSONL file into a list of entries."""
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return entries


def _session_preview(entries: list[dict], max_len: int = 100) -> str:
    """Build a preview string from session entries."""
    topics = []
    for e in entries:
        if e.get("role") == "user":
            text = e.get("content", "").replace("\n", " ").strip()
            if text:
                topics.append(text[:max_len])
            if len(topics) >= 3:
                break
    return " / ".join(topics) if topics else "(空)"


# --- Tools for the AI ---


@tool(
    "search_sessions",
    "Search past conversation sessions by keyword. Returns matching messages with surrounding context.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keyword or phrase",
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
    """Search through session logs for matching messages with context."""
    query = args["query"].lower()
    limit = args.get("limit", 10)
    sessions_dir = get_sessions_dir()

    if not sessions_dir.exists():
        return {"content": [{"type": "text", "text": "No session logs found."}]}

    results = []
    session_files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)

    for session_file in session_files:
        session_name = session_file.stem
        entries = _parse_session_file(session_file)

        for i, entry in enumerate(entries):
            content = entry.get("content", "")
            if query not in content.lower():
                continue

            # Include surrounding context (1 message before, 1 after)
            context_lines = []
            if i > 0:
                prev = entries[i - 1]
                context_lines.append(
                    f"  [{prev['role']}] {prev['content'][:150]}"
                )
            context_lines.append(
                f"  **[{entry['role']}] {content[:300]}**"
            )
            if i + 1 < len(entries):
                nxt = entries[i + 1]
                context_lines.append(
                    f"  [{nxt['role']}] {nxt['content'][:150]}"
                )

            results.append(
                f"Session {session_name} ({entry.get('timestamp', '?')}):\n"
                + "\n".join(context_lines)
            )
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    if not results:
        return {"content": [{"type": "text", "text": f"No matches found for '{args['query']}'."}]}

    return {"content": [{"type": "text", "text": "\n\n---\n\n".join(results)}]}


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
    sessions_dir = get_sessions_dir()

    if not sessions_dir.exists():
        return {"content": [{"type": "text", "text": "No session logs found."}]}

    session_files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)[:limit]

    if not session_files:
        return {"content": [{"type": "text", "text": "No session logs found."}]}

    lines = ["Recent sessions:\n"]
    for session_file in session_files:
        entries = _parse_session_file(session_file)
        preview = _session_preview(entries)
        user_count = sum(1 for e in entries if e.get("role") == "user")
        lines.append(f"- **{session_file.stem}** ({len(entries)} msgs, {user_count} from user): {preview}")

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "read_session",
    "Read the full content of a specific past conversation session.",
    {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Session ID (filename without .jsonl, e.g. '2026-03-06_12-00-00')",
            },
        },
        "required": ["session_id"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def read_session(args: dict[str, Any]) -> dict[str, Any]:
    """Read a specific session's full conversation."""
    session_id = args["session_id"]
    sessions_dir = get_sessions_dir()
    session_file = sessions_dir / f"{session_id}.jsonl"

    if not session_file.exists():
        return {"content": [{"type": "text", "text": f"Session '{session_id}' not found."}]}

    entries = _parse_session_file(session_file)
    if not entries:
        return {"content": [{"type": "text", "text": f"Session '{session_id}' is empty."}]}

    lines = [f"Session: {session_id} ({len(entries)} messages)\n"]
    for entry in entries:
        ts = entry.get("timestamp", "?")
        role = entry.get("role", "?")
        content = entry.get("content", "")
        lines.append(f"[{ts}] **{role}**: {content}")

    return {"content": [{"type": "text", "text": "\n\n".join(lines)}]}


# --- History log helpers ---


def append_history(entry: str) -> None:
    """Append a timestamped entry to the history log file."""
    history_path = get_history_path()
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(entry.rstrip() + "\n\n")


@tool(
    "search_history",
    "Search the append-only history log (HISTORY.md) by keyword. "
    "Each entry starts with [YYYY-MM-DD HH:MM]. Faster than searching session logs.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search keyword or phrase",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 20)",
            },
        },
        "required": ["query"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def search_history(args: dict[str, Any]) -> dict[str, Any]:
    """Search through the history log for matching entries."""
    query = args["query"].lower()
    limit = args.get("limit", 20)
    history_path = get_history_path()

    if not history_path.exists():
        return {"content": [{"type": "text", "text": "No history log found."}]}

    content = history_path.read_text(encoding="utf-8")
    if not content.strip():
        return {"content": [{"type": "text", "text": "History log is empty."}]}

    # Split into entries (each starts with [YYYY-MM-DD HH:MM])
    entries = []
    current = ""
    for line in content.split("\n"):
        if line.startswith("[") and len(line) > 17 and line[17:18] == "]":
            if current.strip():
                entries.append(current.strip())
            current = line
        else:
            current += "\n" + line
    if current.strip():
        entries.append(current.strip())

    # Search
    results = [e for e in entries if query in e.lower()][-limit:]

    if not results:
        return {"content": [{"type": "text", "text": f"No matches found for '{args['query']}' in history."}]}

    return {"content": [{"type": "text", "text": "\n\n---\n\n".join(results)}]}
