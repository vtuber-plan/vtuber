# Memory System Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor vtuber's memory system to match nanobot's proven architecture with session-per-channel management and dual-layer memory.

**Architecture:** Replace time-based session IDs with channel-based keys, consolidate four-layer memory into dual-layer (MEMORY.md + HISTORY.md), and use tool-based consolidation instead of free-text parsing.

**Tech Stack:** Python 3.12, dataclasses, pathlib, asyncio, claude-agent-sdk

---

## Task 1: Update Config Paths

**Files:**
- Modify: `src/vtuber/config.py:1-50`

**Step 1: Add new memory directory functions**

```python
def get_memory_dir() -> Path:
    """Get memory directory path."""
    return get_config_dir() / "memory"

def get_long_term_memory_path() -> Path:
    """Get MEMORY.md path (long-term memory)."""
    return get_memory_dir() / "MEMORY.md"

def get_history_path() -> Path:
    """Get HISTORY.md path (event log)."""
    return get_memory_dir() / "HISTORY.md"
```

**Step 2: Ensure memory directory creation**

In `ensure_config_dir()`, add after creating config_dir:
```python
memory_dir = config_dir / "memory"
memory_dir.mkdir(exist_ok=True)
```

**Step 3: Verify paths are correct**

Run: `uv run python -c "from vtuber.config import get_memory_dir, get_long_term_memory_path, get_history_path; print(get_memory_dir(), get_long_term_memory_path(), get_history_path())"`
Expected: Paths ending in `memory`, `memory/MEMORY.md`, `memory/HISTORY.md`

**Step 4: Commit**

```bash
git add src/vtuber/config.py
git commit -m "feat(config): add memory directory paths"
```

---

## Task 2: Create Session Data Model

**Files:**
- Modify: `src/vtuber/tools/memory.py:1-50`

**Step 1: Add Session dataclass after imports**

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
```

**Step 2: Write test for Session**

Create: `tests/test_session.py`

```python
from vtuber.tools.memory import Session
from datetime import datetime

def test_session_creation():
    session = Session(key="cli:main")
    assert session.key == "cli:main"
    assert session.messages == []
    assert session.last_consolidated == 0

def test_session_add_message():
    session = Session(key="test:123")
    session.add_message("user", "Hello")
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["content"] == "Hello"
    assert "timestamp" in session.messages[0]
```

**Step 3: Run tests**

Run: `uv run pytest tests/test_session.py -v`
Expected: PASS (2 tests)

**Step 4: Commit**

```bash
git add src/vtuber/tools/memory.py tests/test_session.py
git commit -m "feat(memory): add Session dataclass"
```

---

## Task 3: Create SessionManager Class

**Files:**
- Modify: `src/vtuber/tools/memory.py:50-150`

**Step 1: Add helper function for safe filenames**

```python
def _safe_filename(key: str) -> str:
    """Convert session key to safe filename."""
    return key.replace(":", "_").replace("/", "_")
```

**Step 2: Add SessionManager class**

```python
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
```

**Step 3: Write tests for SessionManager**

Create: `tests/test_session_manager.py`

```python
import tempfile
from pathlib import Path
from vtuber.tools.memory import SessionManager, Session

def test_session_manager_create():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SessionManager(Path(tmpdir))
        session = manager.get_or_create("cli:main")
        assert session.key == "cli:main"
        assert session.messages == []

def test_session_manager_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SessionManager(Path(tmpdir))

        # Create and save
        session = manager.get_or_create("test:123")
        session.add_message("user", "Hello")
        manager.save(session)

        # Clear cache and reload
        manager._cache.clear()
        loaded = manager.get_or_create("test:123")
        assert len(loaded.messages) == 1
        assert loaded.messages[0]["content"] == "Hello"

def test_session_manager_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = SessionManager(Path(tmpdir))

        session1 = manager.get_or_create("cli:main")
        session1.add_message("user", "Test")
        manager.save(session1)

        sessions = manager.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["key"] == "cli:main"
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_session_manager.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/vtuber/tools/memory.py tests/test_session_manager.py
git commit -m "feat(memory): add SessionManager class"
```

---

## Task 4: Refactor Memory Tools

**Files:**
- Modify: `src/vtuber/tools/memory.py:150-277`

**Step 1: Remove search_history tool**

Delete the entire `search_history` function and its decorator (lines ~224-276).

**Step 2: Update search_sessions to use SessionManager**

```python
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

    manager = SessionManager(sessions_dir)
    results = []

    for session_info in manager.list_sessions():
        session = manager.get_or_create(session_info["key"])

        for i, entry in enumerate(session.messages):
            content = entry.get("content", "")
            if query not in content.lower():
                continue

            # Include surrounding context
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
        return {"content": [{"type": "text", "text": f"No matches found for '{args['query']}'."}]}

    return {"content": [{"type": "text", "text": "\n\n---\n\n".join(results)}]}
```

**Step 3: Update list_sessions to use SessionManager**

```python
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

    manager = SessionManager(sessions_dir)
    sessions = manager.list_sessions()[:limit]

    if not sessions:
        return {"content": [{"type": "text", "text": "No session logs found."}]}

    lines = ["Recent sessions:\n"]
    for session_info in sessions:
        session = manager.get_or_create(session_info["key"])
        user_count = sum(1 for m in session.messages if m.get("role") == "user")

        # Build preview from first user messages
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

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}
```

**Step 4: Update read_session to use SessionManager**

```python
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
    sessions_dir = get_sessions_dir()

    manager = SessionManager(sessions_dir)
    session = manager.get_or_create(session_id)

    if not session.messages:
        return {"content": [{"type": "text", "text": f"Session '{session_id}' is empty or not found."}]}

    lines = [f"Session: {session_id} ({len(session.messages)} messages)\n"]
    for entry in session.messages:
        ts = entry.get("timestamp", "?")
        role = entry.get("role", "?")
        content = entry.get("content", "")
        lines.append(f"[{ts}] **{role}**: {content}")

    return {"content": [{"type": "text", "text": "\n\n".join(lines)}]}
```

**Step 5: Remove old helper functions**

Delete:
- `_parse_session_file()`
- `_session_preview()`
- `create_session_id()`
- `log_message()`
- `append_history()`

These are now handled by SessionManager and direct file operations.

**Step 6: Update exports in __init__.py**

Modify: `src/vtuber/tools/__init__.py`

```python
from vtuber.tools.memory import (
    search_sessions,
    list_sessions,
    read_session,
    Session,
    SessionManager,
)

__all__ = [
    "search_sessions",
    "list_sessions",
    "read_session",
    "Session",
    "SessionManager",
]
```

**Step 7: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

**Step 8: Commit**

```bash
git add src/vtuber/tools/memory.py src/vtuber/tools/__init__.py
git commit -m "refactor(memory): use SessionManager, remove search_history"
```

---

## Task 5: Update System Prompt

**Files:**
- Modify: `src/vtuber/persona.py:8-55`

**Step 1: Replace TOOLS_SECTION with simplified version**

```python
TOOLS_SECTION = """## Memory System

- `memory/MEMORY.md` — Long-term facts (preferences, context). Always in your context.
- `memory/HISTORY.md` — Append-only event log. NOT loaded. Search with grep.

## Search Past Events

Use Bash tool:
```bash
grep -i "keyword" ~/.vtuber/memory/HISTORY.md
```

## When to Update MEMORY.md

Write important facts immediately using Read/Write/Edit:
- User preferences
- Project context
- Important relationships

## Auto-consolidation

Old conversations are automatically summarized to HISTORY.md and MEMORY.md. You don't manage this."""
```

**Step 2: Update build_system_prompt to use new paths**

The function should already work, just verify it uses `get_long_term_memory_path()` and `get_history_path()`.

**Step 3: Update LONG_TERM_MEMORY_HEADER**

```python
LONG_TERM_MEMORY_HEADER = """## Long-term Memory

The following is your long-term memory from MEMORY.md:

"""
```

**Step 4: Test prompt building**

Run: `uv run python -c "from vtuber.persona import build_system_prompt; from vtuber.config import get_persona_path, get_user_path; print(build_system_prompt(get_persona_path(), get_user_path())[:500])"`
Expected: Output contains "## Memory System" and mentions MEMORY.md and HISTORY.md

**Step 5: Commit**

```bash
git add src/vtuber/persona.py
git commit -m "refactor(persona): simplify memory system prompt"
```

---

## Task 6: Update Heartbeat Consolidation

**Files:**
- Modify: `src/vtuber/daemon/heartbeat.py:1-280`

**Step 1: Add save_memory tool definition at top**

After imports, add:

```python
_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]
```

**Step 2: Refactor _consolidate method**

Replace the entire `_consolidate` method with:

```python
async def _consolidate(self):
    """Auto-consolidate session messages into MEMORY.md + HISTORY.md via tool call."""
    from vtuber.config import get_sessions_dir
    from vtuber.tools.memory import SessionManager

    self._consolidation_running = True
    try:
        sessions_dir = get_sessions_dir()
        manager = SessionManager(sessions_dir)
        session = manager.get_or_create(self.session_id)

        # Check if consolidation needed
        keep_count = 25  # Keep last 25 messages
        if len(session.messages) <= keep_count:
            return
        if len(session.messages) - session.last_consolidated <= 0:
            return

        old_messages = session.messages[session.last_consolidated:-keep_count]
        if not old_messages:
            return

        logger.info(
            "[consolidation] starting: %d messages to consolidate",
            len(old_messages),
        )

        # Build transcript
        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            ts = m.get("timestamp", "?")[:16]
            role = m.get("role", "?")
            content = m.get("content", "")
            lines.append(f"[{ts}] {role.upper()}: {content}")

        if not lines:
            return

        memory_path = get_long_term_memory_path()
        current_memory = ""
        if memory_path.exists():
            current_memory = memory_path.read_text(encoding="utf-8").strip()

        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{chr(10).join(lines)}"""

        # Call LLM with save_memory tool
        from claude_agent_sdk import query as sdk_query
        from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

        options = build_agent_options(
            system_prompt="You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation.",
            tools=_SAVE_MEMORY_TOOL,
            tool_choice={"type": "tool", "name": "save_memory"},
            include_mcp_tools=False,
            include_preset_tools=False,
            include_schedule=False,
        )

        tool_args = None
        try:
            async for msg in sdk_query(prompt=prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock) and block.name == "save_memory":
                            tool_args = block.input
                            break
                    if tool_args:
                        break
        except Exception as e:
            logger.error("[consolidation] error: %s", e, exc_info=True)
            return

        if not tool_args:
            logger.warning("[consolidation] LLM did not call save_memory tool")
            return

        # Process results
        from vtuber.config import get_history_path

        if entry := tool_args.get("history_entry"):
            if not isinstance(entry, str):
                entry = json.dumps(entry, ensure_ascii=False)
            history_path = get_history_path()
            with open(history_path, "a", encoding="utf-8") as f:
                f.write(entry.rstrip() + "\n\n")

        if update := tool_args.get("memory_update"):
            if not isinstance(update, str):
                update = json.dumps(update, ensure_ascii=False)
            if update != current_memory:
                memory_path.write_text(update, encoding="utf-8")

        # Update session metadata
        session.last_consolidated = len(session.messages) - keep_count
        manager.save(session)

        logger.info("[consolidation] completed: consolidated up to message %d", session.last_consolidated)

    except Exception as e:
        logger.error("[consolidation] error: %s", e, exc_info=True)
    finally:
        self._consolidation_running = False
```

**Step 3: Remove consolidation state file handling**

Delete references to `get_consolidation_state_path()` and consolidation state JSON file.

**Step 4: Add json import**

At top of file: `import json`

**Step 5: Update imports**

Remove: `from vtuber.daemon.agent_query import collect_oneshot`

Add: `from vtuber.config import get_long_term_memory_path`

**Step 6: Commit**

```bash
git add src/vtuber/daemon/heartbeat.py
git commit -m "refactor(heartbeat): use tool-based consolidation"
```

---

## Task 7: Update Daemon Server

**Files:**
- Modify: `src/vtuber/daemon/server.py:1-450`

**Step 1: Update session management**

Replace `self.session_id = create_session_id()` with:

```python
self.session_id = "cli:main"  # Default CLI session
self.session_manager = SessionManager(get_sessions_dir())
```

**Step 2: Add SessionManager import**

```python
from vtuber.tools.memory import SessionManager
```

**Step 3: Remove old imports**

Remove:
- `from vtuber.tools.memory import create_session_id, log_message`

**Step 4: Update message logging**

Replace `log_message()` calls with:

```python
session = self.session_manager.get_or_create(self.session_id)
session.add_message("user", content, sender=sender)
self.session_manager.save(session)
```

And for assistant:

```python
session = self.session_manager.get_or_create(self.session_id)
session.add_message("assistant", full_text.strip())
self.session_manager.save(session)
```

**Step 5: Update HeartbeatManager initialization**

```python
self._heartbeat = HeartbeatManager(
    self.gateway, self.session_id, config.heartbeat_interval,
)
```

**Step 6: Update status command**

In `_handle_status()`, update session display:

```python
session = self.session_manager.get_or_create(self.session_id)
status_info = {
    "session_id": session.key,
    "messages": len(session.messages),
    "last_consolidated": session.last_consolidated,
    ...
}
```

**Step 7: Test daemon startup**

Run: `uv run vtuber start`
Expected: Daemon starts without errors

**Step 8: Commit**

```bash
git add src/vtuber/daemon/server.py
git commit -m "refactor(server): use SessionManager for session handling"
```

---

## Task 8: Create Migration Script

**Files:**
- Create: `scripts/migrate_memory.py`

**Step 1: Write migration script**

```python
#!/usr/bin/env python3
"""Migrate old memory files to new structure."""

import shutil
from pathlib import Path

def migrate():
    config_dir = Path.home() / ".vtuber"

    # Create memory directory
    memory_dir = config_dir / "memory"
    memory_dir.mkdir(exist_ok=True)

    # Migrate long_term_memory.md -> memory/MEMORY.md
    old_memory = config_dir / "long_term_memory.md"
    new_memory = memory_dir / "MEMORY.md"
    if old_memory.exists() and not new_memory.exists():
        shutil.move(str(old_memory), str(new_memory))
        print(f"✓ Migrated {old_memory} -> {new_memory}")

    # Migrate history.md -> memory/HISTORY.md
    old_history = config_dir / "history.md"
    new_history = memory_dir / "HISTORY.md"
    if old_history.exists() and not new_history.exists():
        shutil.move(str(old_history), str(new_history))
        print(f"✓ Migrated {old_history} -> {new_history}")

    # Remove consolidation state file (no longer needed)
    state_file = config_dir / "consolidation_state.json"
    if state_file.exists():
        state_file.unlink()
        print(f"✓ Removed obsolete {state_file}")

    # Sessions don't need migration (format change is too significant)
    # Old sessions can be manually reviewed if needed
    old_sessions = config_dir / "memory" / "sessions"
    if old_sessions.exists():
        backup = config_dir / "sessions_backup_old_format"
        if not backup.exists():
            shutil.move(str(old_sessions), str(backup))
            print(f"✓ Backed up old sessions to {backup}")
            print("  Old session format is incompatible. Backup preserved for manual review.")

    print("\n✅ Migration complete!")

if __name__ == "__main__":
    migrate()
```

**Step 2: Make script executable**

Run: `chmod +x scripts/migrate_memory.py`

**Step 3: Test migration**

Run: `uv run python scripts/migrate_memory.py`
Expected: Files moved successfully

**Step 4: Commit**

```bash
git add scripts/migrate_memory.py
git commit -m "feat(scripts): add memory migration script"
```

---

## Task 9: Update Config Module

**Files:**
- Modify: `src/vtuber/config.py`

**Step 1: Remove get_consolidation_state_path()**

Delete the function (no longer needed).

**Step 2: Update ensure_sessions_dir()**

Make sure it creates sessions directory in the right place.

**Step 3: Commit**

```bash
git add src/vtuber/config.py
git commit -m "refactor(config): remove consolidation state path"
```

---

## Task 10: Update CLI Provider

**Files:**
- Modify: `src/vtuber/providers/cli.py`

**Step 1: Update session ID display**

Replace any display of timestamp-based session IDs with channel-based keys.

**Step 2: Commit**

```bash
git add src/vtuber/providers/cli.py
git commit -m "refactor(cli): update session display"
```

---

## Task 11: Integration Testing

**Files:**
- Test: Manual testing

**Step 1: Test fresh start**

```bash
rm -rf ~/.vtuber
uv run vtuber start
```

Expected: Daemon creates new directory structure with memory/ and sessions/

**Step 2: Test chat**

```bash
uv run vtuber chat
```

Send a few messages, exit, then check:
```bash
ls ~/.vtuber/sessions/
cat ~/.vtuber/sessions/cli_main.jsonl
```

Expected: Session file created with proper format

**Step 3: Test consolidation**

Send >50 messages in chat, then check:
```bash
cat ~/.vtuber/memory/MEMORY.md
cat ~/.vtuber/memory/HISTORY.md
```

Expected: Files updated with consolidation results

**Step 4: Test migration**

```bash
# Create old structure
mkdir -p ~/.vtuber
echo "# Old memory" > ~/.vtuber/long_term_memory.md
echo "[2026-03-08 10:00] Old event" > ~/.vtuber/history.md

# Run migration
uv run python scripts/migrate_memory.py

# Verify
ls ~/.vtuber/memory/
cat ~/.vtuber/memory/MEMORY.md
```

Expected: Old files moved to memory/ directory

**Step 5: Final commit**

```bash
git add -A
git commit -m "test: verify memory system refactor works end-to-end"
```

---

## Success Criteria

- [ ] Session files use `channel:chat_id` naming
- [ ] MEMORY.md and HISTORY.md exist in `~/.vtuber/memory/`
- [ ] Consolidation uses `save_memory` tool
- [ ] `search_history` tool removed
- [ ] System prompt simplified
- [ ] All tests pass
- [ ] Manual testing successful
- [ ] Migration script works

## References

- Design doc: `docs/plans/2026-03-08-memory-system-refactor-design.md`
- nanobot source: `/Users/lex/Codes/AgentWorkSpace/nanobot/`
