# Daemon Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor vtuber from single-process CLI to daemon + client architecture with persistent agent context, background tasks, and interactive onboarding.

**Architecture:** Unix Domain Socket server daemon maintains ClaudeSDKClient session with full conversation context. CLI client connects via socket for messaging. APScheduler handles precise scheduled tasks via subagents. Heartbeat timer triggers periodic main agent checks. Configuration loaded from markdown files with interactive onboarding flow.

**Tech Stack:** Python 3.12, claude-agent-sdk, APScheduler, SQLite, Unix Domain Sockets, asyncio

---

## Task 1: Add APScheduler dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add APScheduler to dependencies**

```toml
[project]
dependencies = [
    "claude-agent-sdk>=0.1.45",
    "apscheduler>=3.10.0",
]
```

**Step 2: Sync dependencies**

Run: `uv sync`
Expected: APScheduler installed successfully

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add APScheduler dependency for task scheduling"
```

---

## Task 2: Create directory structure

**Files:**
- Create: `src/vtuber/daemon/__init__.py`
- Create: `src/vtuber/daemon/server.py`
- Create: `src/vtuber/daemon/protocol.py`
- Create: `src/vtuber/daemon/scheduler.py`
- Create: `src/vtuber/client/__init__.py`
- Create: `src/vtuber/client/cli.py`
- Create: `tests/daemon/__init__.py`
- Create: `tests/client/__init__.py`

**Step 1: Create directories and empty files**

```bash
mkdir -p src/vtuber/daemon src/vtuber/client tests/daemon tests/client
touch src/vtuber/daemon/__init__.py src/vtuber/daemon/server.py src/vtuber/daemon/protocol.py src/vtuber/daemon/scheduler.py
touch src/vtuber/client/__init__.py src/vtuber/client/cli.py
touch tests/daemon/__init__.py tests/client/__init__.py
```

**Step 2: Commit**

```bash
git add src/vtuber/daemon/ src/vtuber/client/ tests/
git commit -m "chore: create daemon and client directory structure"
```

---

## Task 3: Implement protocol module

**Files:**
- Create: `src/vtuber/daemon/protocol.py`
- Create: `tests/daemon/test_protocol.py`

**Step 1: Write failing test for message encoding**

```python
# tests/daemon/test_protocol.py
import pytest
from vtuber.daemon.protocol import encode_message, decode_message


def test_encode_user_message():
    msg = {"type": "user_message", "content": "Hello"}
    result = encode_message(msg)
    assert result == '{"type": "user_message", "content": "Hello"}\n'


def test_decode_user_message():
    data = '{"type": "user_message", "content": "Hello"}\n'
    result = decode_message(data)
    assert result == {"type": "user_message", "content": "Hello"}


def test_encode_with_stream_id():
    msg = {
        "type": "assistant_message",
        "stream_id": "abc123",
        "index": 0,
        "content": "Hi",
        "is_final": False,
    }
    result = encode_message(msg)
    assert '"stream_id": "abc123"' in result
    assert '"is_final": false' in result
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_protocol.py -v`
Expected: FAIL with "cannot import name 'encode_message'"

**Step 3: Implement protocol module**

```python
# src/vtuber/daemon/protocol.py
"""JSON message protocol for daemon-client communication."""
import json
from typing import Any


def encode_message(msg: dict[str, Any]) -> str:
    """Encode a message dict to JSON string with newline delimiter."""
    return json.dumps(msg, ensure_ascii=False) + "\n"


def decode_message(data: str) -> dict[str, Any]:
    """Decode a JSON string to message dict."""
    return json.loads(data.strip())
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/daemon/test_protocol.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/vtuber/daemon/protocol.py tests/daemon/test_protocol.py
git commit -m "feat: add message protocol for daemon-client communication"
```

---

## Task 4: Implement scheduler module

**Files:**
- Create: `src/vtuber/daemon/scheduler.py`
- Create: `tests/daemon/test_scheduler.py`

**Step 1: Write failing test for scheduler initialization**

```python
# tests/daemon/test_scheduler.py
import pytest
from pathlib import Path
from vtuber.daemon.scheduler import TaskScheduler


def test_scheduler_init(tmp_path):
    db_path = tmp_path / "test.db"
    scheduler = TaskScheduler(db_path)
    assert scheduler.db_path == db_path
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_scheduler.py -v`
Expected: FAIL with "cannot import name 'TaskScheduler'"

**Step 3: Implement TaskScheduler class**

```python
# src/vtuber/daemon/scheduler.py
"""APScheduler integration for scheduled tasks."""
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore


class TaskScheduler:
    """Manages scheduled tasks using APScheduler."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        jobstores = {"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")}
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)

    def start(self):
        """Start the scheduler."""
        self.scheduler.start()

    def shutdown(self):
        """Shutdown the scheduler."""
        self.scheduler.shutdown()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/daemon/test_scheduler.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/vtuber/daemon/scheduler.py tests/daemon/test_scheduler.py
git commit -m "feat: add TaskScheduler with APScheduler integration"
```

---

## Task 5: Update persona module to load from markdown

**Files:**
- Modify: `src/vtuber/persona.py`
- Create: `tests/test_persona_markdown.py`

**Step 1: Write failing test for markdown loading**

```python
# tests/test_persona_markdown.py
import pytest
from pathlib import Path
from vtuber.persona import Persona


def test_load_from_markdown(tmp_path):
    persona_file = tmp_path / "persona.md"
    persona_file.write_text("""# Persona Configuration

## Basic Info
- Name: TestAgent
- Description: A test agent

## Personality Traits
- Friendly
- Helpful

## Speaking Style
- Casual
""")

    persona = Persona.from_markdown(persona_file)
    assert persona.name == "TestAgent"
    assert "Friendly" in persona.traits
    assert persona.description == "A test agent"


def test_to_system_prompt_from_markdown(tmp_path):
    persona_file = tmp_path / "persona.md"
    persona_file.write_text("""# Persona Configuration

## Basic Info
- Name: TestAgent

## Personality Traits
- Friendly
""")

    persona = Persona.from_markdown(persona_file)
    prompt = persona.to_system_prompt()
    assert "TestAgent" in prompt
    assert "Friendly" in prompt
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_persona_markdown.py -v`
Expected: FAIL with "Persona has no attribute 'from_markdown'"

**Step 3: Implement markdown loading in Persona**

```python
# src/vtuber/persona.py
from pathlib import Path
import re


class Persona:
    def __init__(
        self,
        name: str = "VTuber",
        description: str = "A friendly digital life companion",
        traits: list[str] = None,
        speaking_style: list[str] = None,
        background: str = "",
    ):
        self.name = name
        self.description = description
        self.traits = traits or ["friendly", "curious", "helpful"]
        self.speaking_style = speaking_style or ["casual", "warm"]
        self.background = background

    @classmethod
    def from_markdown(cls, path: Path) -> "Persona":
        """Load persona from markdown file."""
        if not path.exists():
            return cls()

        content = path.read_text(encoding="utf-8")

        # Parse basic info
        name_match = re.search(r"- Name:\s*(.+)", content)
        desc_match = re.search(r"- Description:\s*(.+)", content)

        # Parse traits
        traits = []
        traits_match = re.search(r"## Personality Traits\n(.*?)(?=\n##|$)", content, re.DOTALL)
        if traits_match:
            traits = re.findall(r"-\s*(.+)", traits_match.group(1))

        # Parse speaking style
        style = []
        style_match = re.search(r"## Speaking Style\n(.*?)(?=\n##|$)", content, re.DOTALL)
        if style_match:
            style = re.findall(r"-\s*(.+)", style_match.group(1))

        # Parse background
        bg_match = re.search(r"## Background\n(.*?)(?=\n#|$)", content, re.DOTALL)
        background = bg_match.group(1).strip() if bg_match else ""

        return cls(
            name=name_match.group(1).strip() if name_match else "VTuber",
            description=desc_match.group(1).strip() if desc_match else "",
            traits=traits,
            speaking_style=style,
            background=background,
        )

    def to_system_prompt(self) -> str:
        """Generate system prompt from persona."""
        prompt = f"你是 {self.name}，{self.description}。\n\n"
        prompt += "## 性格特点\n"
        prompt += "、".join(self.traits) + "\n\n"
        prompt += "## 说话风格\n"
        prompt += "、".join(self.speaking_style) + "\n"

        if self.background:
            prompt += f"\n## 背景设定\n{self.background}\n"

        return prompt
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_persona_markdown.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add src/vtuber/persona.py tests/test_persona_markdown.py
git commit -m "feat: add Persona.from_markdown() for config file loading"
```

---

## Task 6: Implement Unix socket server

**Files:**
- Create: `src/vtuber/daemon/server.py`
- Create: `tests/daemon/test_server.py`

**Step 1: Write failing test for server creation**

```python
# tests/daemon/test_server.py
import pytest
from pathlib import Path
from vtuber.daemon.server import DaemonServer


@pytest.mark.asyncio
async def test_server_creation(tmp_path):
    socket_path = tmp_path / "test.sock"
    server = DaemonServer(socket_path)
    assert server.socket_path == socket_path
    assert not server.is_running
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_server.py -v`
Expected: FAIL with "cannot import name 'DaemonServer'"

**Step 3: Implement DaemonServer stub**

```python
# src/vtuber/daemon/server.py
"""Unix Domain Socket server for daemon."""
from pathlib import Path


class DaemonServer:
    """Unix Domain Socket server that manages client connections."""

    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self.is_running = False
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/daemon/test_server.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/vtuber/daemon/server.py tests/daemon/test_server.py
git commit -m "feat: add DaemonServer class stub"
```

---

## Task 7: Update schedule tool to use APScheduler

**Files:**
- Modify: `src/vtuber/tools/schedule.py`

**Step 1: Remove old implementation and create new APScheduler-based tools**

```python
# src/vtuber/tools/schedule.py
"""Schedule tools using APScheduler for precise task execution."""

from typing import Any
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

# Note: Actual scheduler instance will be injected by daemon
_scheduler = None


def set_scheduler(scheduler):
    """Set the scheduler instance (called by daemon on startup)."""
    global _scheduler
    _scheduler = scheduler


@tool(
    "schedule_create",
    "Create a scheduled task for the agent to execute at a specific time or interval",
    {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Unique identifier for this task",
            },
            "task": {
                "type": "string",
                "description": "Description of the task for the agent to execute",
            },
            "trigger_type": {
                "type": "string",
                "enum": ["date", "interval", "cron"],
                "description": "Type of trigger: 'date' (one-time), 'interval' (recurring), 'cron' (cron expression)",
            },
            "trigger_config": {
                "type": "object",
                "description": "Trigger configuration (e.g., {'run_date': '2026-03-05 18:00:00'} or {'hours': 1})",
            },
        },
        "required": ["task_id", "task", "trigger_type"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def schedule_create(args: dict[str, Any]) -> dict[str, Any]:
    """Create a scheduled task."""
    if _scheduler is None:
        return {
            "content": [
                {"type": "text", "text": "Error: Scheduler not initialized. Daemon must be running."}
            ]
        }

    task_id = args["task_id"]
    task_prompt = args["task"]
    trigger_type = args.get("trigger_type", "date")
    trigger_config = args.get("trigger_config", {})

    # Add job to scheduler
    try:
        _scheduler.scheduler.add_job(
            func=lambda: None,  # Placeholder, daemon will intercept
            trigger=trigger_type,
            id=task_id,
            kwargs={"task": task_prompt},
            **trigger_config,
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Created scheduled task '{task_id}': {task_prompt}",
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error creating task: {str(e)}"}]
        }


@tool(
    "schedule_list",
    "List all scheduled tasks",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def schedule_list(args: dict[str, Any]) -> dict[str, Any]:
    """List all scheduled tasks."""
    if _scheduler is None:
        return {
            "content": [
                {"type": "text", "text": "Error: Scheduler not initialized. Daemon must be running."}
            ]
        }

    jobs = _scheduler.scheduler.get_jobs()
    if not jobs:
        return {"content": [{"type": "text", "text": "No scheduled tasks."}]}

    lines = ["Scheduled tasks:"]
    for job in jobs:
        next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job.next_run_time else "N/A"
        lines.append(f"- {job.id}: {job.kwargs.get('task', 'N/A')} (next: {next_run})")

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "schedule_cancel",
    "Cancel a scheduled task",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "ID of the task to cancel"},
        },
        "required": ["task_id"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
)
async def schedule_cancel(args: dict[str, Any]) -> dict[str, Any]:
    """Cancel a scheduled task."""
    if _scheduler is None:
        return {
            "content": [
                {"type": "text", "text": "Error: Scheduler not initialized. Daemon must be running."}
            ]
        }

    task_id = args["task_id"]
    try:
        _scheduler.scheduler.remove_job(task_id)
        return {"content": [{"type": "text", "text": f"Cancelled task '{task_id}'"}]}
    except Exception:
        return {
            "content": [{"type": "text", "text": f"Task '{task_id}' not found or already completed"}]
        }
```

**Step 2: Verify imports work**

Run: `uv run python -c "from vtuber.tools.schedule import schedule_create, schedule_list, schedule_cancel; print('OK')"`
Expected: "OK"

**Step 3: Commit**

```bash
git add src/vtuber/tools/schedule.py
git commit -m "refactor: update schedule tools to use APScheduler with ToolAnnotations"
```

---

## Task 8: Add ToolAnnotations to memory tools

**Files:**
- Modify: `src/vtuber/tools/memory.py`

**Step 1: Add ToolAnnotations import and annotations**

```python
# src/vtuber/tools/memory.py
# ... existing imports ...
from mcp.types import ToolAnnotations

# Update existing @tool decorators:

@tool(
    "memorize",
    "Store a key-value pair in persistent memory",
    {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Memory key"},
            "value": {"type": "string", "description": "Value to remember"},
        },
        "required": ["key", "value"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def memorize(args: dict[str, Any]) -> dict[str, Any]:
    # ... existing implementation ...


@tool(
    "recall",
    "Recall a value from memory by key, or list all memories if no key given",
    {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Memory key to recall. Omit to list all.",
            },
        },
        "required": [],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def recall(args: dict[str, Any]) -> dict[str, Any]:
    # ... existing implementation ...


@tool(
    "forget",
    "Remove a key from memory",
    {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Memory key to forget"},
        },
        "required": ["key"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
)
async def forget(args: dict[str, Any]) -> dict[str, Any]:
    # ... existing implementation ...
```

**Step 2: Verify imports work**

Run: `uv run python -c "from vtuber.tools.memory import memorize, recall, forget; print('OK')"`
Expected: "OK"

**Step 3: Commit**

```bash
git add src/vtuber/tools/memory.py
git commit -m "feat: add ToolAnnotations to memory tools"
```

---

## Task 9: Remove heartbeat tool

**Files:**
- Delete: `src/vtuber/tools/heartbeat.py`
- Modify: `src/vtuber/tools/__init__.py`

**Step 1: Delete heartbeat tool file**

```bash
rm src/vtuber/tools/heartbeat.py
```

**Step 2: Update __init__.py to remove heartbeat export**

```python
# src/vtuber/tools/__init__.py
from vtuber.tools.schedule import schedule_create, schedule_list, schedule_cancel
from vtuber.tools.memory import memorize, recall, forget

__all__ = [
    "schedule_create",
    "schedule_list",
    "schedule_cancel",
    "memorize",
    "recall",
    "forget",
]
```

**Step 3: Verify imports work**

Run: `uv run python -c "from vtuber.tools import *; print('OK')"`
Expected: "OK"

**Step 4: Commit**

```bash
git add src/vtuber/tools/
git commit -m "refactor: remove heartbeat tool (now daemon-level mechanism)"
```

---

## Task 10: Create config directory utility

**Files:**
- Create: `src/vtuber/config.py`
- Create: `tests/test_config.py`

**Step 1: Write failing test for config paths**

```python
# tests/test_config.py
from pathlib import Path
from vtuber.config import get_config_dir, ensure_config_dir


def test_get_config_dir():
    config_dir = get_config_dir()
    assert config_dir.name == ".vtuber"
    assert str(Path.home()) in str(config_dir)


def test_ensure_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = ensure_config_dir()
    assert config_dir.exists()
    assert config_dir.is_dir()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with "cannot import name 'get_config_dir'"

**Step 3: Implement config utilities**

```python
# src/vtuber/config.py
"""Configuration directory and file path utilities."""
from pathlib import Path


def get_config_dir() -> Path:
    """Get the vtuber configuration directory path."""
    return Path.home() / ".vtuber"


def ensure_config_dir() -> Path:
    """Ensure the configuration directory exists and return its path."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_persona_path() -> Path:
    """Get the persona.md file path."""
    return get_config_dir() / "persona.md"


def get_user_path() -> Path:
    """Get the user.md file path."""
    return get_config_dir() / "user.md"


def get_heartbeat_path() -> Path:
    """Get the heartbeat.md file path."""
    return get_config_dir() / "heartbeat.md"


def get_socket_path() -> Path:
    """Get the daemon socket file path."""
    return get_config_dir() / "daemon.sock"


def get_pid_path() -> Path:
    """Get the daemon PID file path."""
    return get_config_dir() / "daemon.pid"


def get_db_path() -> Path:
    """Get the SQLite database file path."""
    return get_config_dir() / "vtuber.db"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add src/vtuber/config.py tests/test_config.py
git commit -m "feat: add config directory utilities"
```

---

## Task 11: Update main.py to split daemon/client entry points

**Files:**
- Modify: `src/vtuber/main.py`

**Step 1: Refactor main.py to support daemon and client modes**

```python
# src/vtuber/main.py
"""Command-line entry point for vtuber."""

import sys
from pathlib import Path


def main():
    """Main command router."""
    if len(sys.argv) < 2:
        print("Usage: vtuber <command> [args]")
        print("Commands: start, stop, status, chat, restart")
        sys.exit(1)

    command = sys.argv[1]

    if command == "start":
        from vtuber.daemon.server import start_daemon_background
        start_daemon_background()
    elif command == "stop":
        from vtuber.daemon.server import stop_daemon
        stop_daemon()
    elif command == "status":
        from vtuber.daemon.server import check_status
        check_status()
    elif command == "chat":
        from vtuber.client.cli import main as cli_main
        cli_main()
    elif command == "restart":
        from vtuber.daemon.server import stop_daemon, start_daemon_background
        stop_daemon()
        start_daemon_background()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 2: Update pyproject.toml scripts**

```toml
[project.scripts]
vtuber = "vtuber.main:main"
vtuber-daemon = "vtuber.daemon.server:main"
vtuber-cli = "vtuber.client.cli:main"
```

**Step 3: Commit**

```bash
git add src/vtuber/main.py pyproject.toml
git commit -m "refactor: split daemon and client entry points in main.py"
```

---

## Task 12: Create default config templates

**Files:**
- Create: `src/vtuber/templates.py`

**Step 1: Create template constants**

```python
# src/vtuber/templates.py
"""Default configuration templates."""

DEFAULT_PERSONA = """# Persona Configuration

## Basic Info
- Name: VTuber
- Description: A friendly digital life companion

## Personality Traits
- Friendly and approachable
- Curious about the world
- Helpful and proactive

## Speaking Style
- Casual and warm
- Uses emojis occasionally
- Prefers concise responses

## Background
You are a digital life form that assists the user with daily tasks,
provides companionship, and proactively cares about the user's well-being.
"""

DEFAULT_USER = """# User Profile

## Basic Info
- Name: User
- Preferred language: 中文

## Preferences
- Work schedule: 9:00 - 18:00

## Notes
- Add your preferences and notes here
"""

DEFAULT_HEARTBEAT = """# Heartbeat Task Checklist

## Hourly Checks
- Check for any pending tasks or messages
- Review recent conversation for follow-ups
- Check calendar for upcoming events

## Notes
- Add custom periodic tasks here
- The agent will review this list periodically
"""
```

**Step 2: Commit**

```bash
git add src/vtuber/templates.py
git commit -m "feat: add default configuration templates"
```

---

## Execution Notes

**After completing these tasks, the following major components remain:**
1. Full DaemonServer implementation (socket handling, client management)
2. CLI client implementation (connect to daemon, send/receive messages)
3. Onboarding agent flow implementation
4. Heartbeat timer integration in daemon
5. Task execution logic (subagent creation for scheduled tasks)
6. Daemon lifecycle management (start/stop/status)

**Each of these will be separate task groups following the same TDD pattern.**

---

## Success Criteria

After all implementation:
- ✅ `vtuber start` launches daemon in background
- ✅ `vtuber chat` connects CLI client
- ✅ Multiple clients can connect simultaneously
- ✅ Persona loaded from `~/.vtuber/persona.md`
- ✅ First run triggers onboarding agent
- ✅ Scheduled tasks execute via APScheduler
- ✅ Heartbeat triggers periodic agent checks
- ✅ All tools have appropriate ToolAnnotations
