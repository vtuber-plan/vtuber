"""Session data layer — conversation storage as JSONL files."""

import json
import logging
import re
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """A conversation session.

    Stores messages in JSONL format for easy persistence.
    Messages are append-only for LLM cache efficiency.
    """

    key: str  # channel:chat_id (e.g., "cli:main", "onebot:private:123")
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


def _safe_filename(key: str) -> str:
    """Convert session key to safe filename."""
    safe = key.replace(":", "_").replace("/", "_")
    safe = re.sub(r'[^\w\-.]', '_', safe)
    safe = safe.strip(".").replace("..", "_")
    return safe or "unnamed"


class SessionManager:
    """Manages conversation sessions stored as JSONL files."""

    _MAX_CACHE = 100

    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._cache: OrderedDict[str, Session] = OrderedDict()

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = _safe_filename(key)
        return self.sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """Get an existing session or create a new one."""
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        while len(self._cache) > self._MAX_CACHE:
            self._cache.popitem(last=False)
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
            logger.error("Failed to load session %s: %s", key, e)
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk atomically."""
        path = self._get_session_path(session.key)

        metadata_line = {
            "_type": "metadata",
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated,
        }

        # Write to temp file in the same directory, then atomic rename
        fd, tmp_path = tempfile.mkstemp(
            dir=self.sessions_dir, suffix=".tmp", prefix=".session-",
        )
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                for msg in session.messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                f.flush()
            Path(tmp_path).replace(path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

        self._cache[session.key] = session
        self._cache.move_to_end(session.key)

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
