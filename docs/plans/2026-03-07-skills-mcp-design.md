# Skills MCP Tool System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a custom Skills system as MCP tools, enabling the vtuber agent to create, invoke, update, delete, and refresh reusable prompt-based skills stored under `~/.vtuber/skills/`.

**Architecture:** Skills are SKILL.md files (YAML frontmatter + markdown body) organized in `~/.vtuber/skills/<name>/` directories. Five MCP tools manage them. Skill descriptions are injected into the system prompt at agent startup. `skill_refresh` uses an `asyncio.Event` to signal the server to rebuild the agent with updated prompts while preserving the conversation via SDK `resume`.

**Tech Stack:** Python 3.12, claude-agent-sdk (`@tool`, `create_sdk_mcp_server`), PyYAML (already in deps), asyncio, shutil

---

### Task 1: Add `get_skills_dir()` helper to config.py

**Files:**
- Modify: `src/vtuber/config.py:95-181`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_get_skills_dir():
    from vtuber.config import get_skills_dir
    result = get_skills_dir()
    assert result == Path.home() / ".vtuber" / "skills"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_get_skills_dir -v`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

Add to `src/vtuber/config.py` after `get_consolidation_state_path()`:

```python
def get_skills_dir() -> Path:
    """Get the skills directory path."""
    return get_config_dir() / "skills"


def ensure_skills_dir() -> Path:
    """Ensure the skills directory exists and return its path."""
    skills_dir = get_skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::test_get_skills_dir -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/vtuber/config.py tests/test_config.py
git commit -m "feat: add get_skills_dir and ensure_skills_dir to config"
```

---

### Task 2: Create `src/vtuber/tools/skills.py` with `build_skill_summary()` and 5 MCP tools

**Files:**
- Create: `src/vtuber/tools/skills.py`
- Test: `tests/test_skills.py`

**Step 1: Write the failing tests**

Create `tests/test_skills.py`:

```python
"""Tests for skills tool module."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def skills_dir(tmp_path):
    """Create a temporary skills directory with sample skills."""
    sd = tmp_path / "skills"
    sd.mkdir()

    # Create a sample skill
    skill_path = sd / "greeting"
    skill_path.mkdir()
    (skill_path / "SKILL.md").write_text(
        "---\nname: greeting\ndescription: A greeting skill\n---\n\nSay hello warmly.\n",
        encoding="utf-8",
    )

    # Create another skill
    skill_path2 = sd / "farewell"
    skill_path2.mkdir()
    (skill_path2 / "SKILL.md").write_text(
        "---\nname: farewell\ndescription: A farewell skill\n---\n\nSay goodbye kindly.\n",
        encoding="utf-8",
    )
    return sd


def test_build_skill_summary_with_skills(skills_dir):
    from vtuber.tools.skills import build_skill_summary

    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = build_skill_summary()

    assert "greeting" in result
    assert "A greeting skill" in result
    assert "farewell" in result
    assert "A farewell skill" in result
    assert "skill_invoke" in result


def test_build_skill_summary_empty(tmp_path):
    from vtuber.tools.skills import build_skill_summary

    empty_dir = tmp_path / "skills"
    empty_dir.mkdir()

    with patch("vtuber.tools.skills.get_skills_dir", return_value=empty_dir):
        result = build_skill_summary()

    assert result == ""


def test_build_skill_summary_no_dir(tmp_path):
    from vtuber.tools.skills import build_skill_summary

    missing = tmp_path / "nonexistent"

    with patch("vtuber.tools.skills.get_skills_dir", return_value=missing):
        result = build_skill_summary()

    assert result == ""


@pytest.mark.asyncio
async def test_skill_invoke(skills_dir):
    from vtuber.tools.skills import skill_invoke

    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_invoke({"skill": "greeting"})

    text = result["content"][0]["text"]
    assert "Say hello warmly." in text


@pytest.mark.asyncio
async def test_skill_invoke_not_found(skills_dir):
    from vtuber.tools.skills import skill_invoke

    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_invoke({"skill": "nonexistent"})

    text = result["content"][0]["text"]
    assert "not found" in text.lower() or "未找到" in text


@pytest.mark.asyncio
async def test_skill_create(tmp_path):
    from vtuber.tools.skills import skill_create

    sd = tmp_path / "skills"
    sd.mkdir()

    with patch("vtuber.tools.skills.get_skills_dir", return_value=sd):
        result = await skill_create({"name": "my-new-skill"})

    text = result["content"][0]["text"]
    assert str(sd / "my-new-skill") in text
    assert (sd / "my-new-skill").is_dir()


@pytest.mark.asyncio
async def test_skill_create_already_exists(skills_dir):
    from vtuber.tools.skills import skill_create

    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_create({"name": "greeting"})

    text = result["content"][0]["text"]
    assert "already exists" in text.lower() or "已存在" in text


@pytest.mark.asyncio
async def test_skill_update(skills_dir):
    from vtuber.tools.skills import skill_update

    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_update({"name": "greeting"})

    text = result["content"][0]["text"]
    assert str(skills_dir / "greeting" / "SKILL.md") in text


@pytest.mark.asyncio
async def test_skill_update_not_found(skills_dir):
    from vtuber.tools.skills import skill_update

    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_update({"name": "nonexistent"})

    text = result["content"][0]["text"]
    assert "not found" in text.lower() or "未找到" in text


@pytest.mark.asyncio
async def test_skill_delete(skills_dir):
    from vtuber.tools.skills import skill_delete

    assert (skills_dir / "greeting").exists()

    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_delete({"name": "greeting"})

    text = result["content"][0]["text"]
    assert not (skills_dir / "greeting").exists()
    assert "greeting" in text


@pytest.mark.asyncio
async def test_skill_delete_not_found(skills_dir):
    from vtuber.tools.skills import skill_delete

    with patch("vtuber.tools.skills.get_skills_dir", return_value=skills_dir):
        result = await skill_delete({"name": "nonexistent"})

    text = result["content"][0]["text"]
    assert "not found" in text.lower() or "未找到" in text


@pytest.mark.asyncio
async def test_skill_refresh():
    import asyncio
    from vtuber.tools.skills import skill_refresh, set_refresh_event

    event = asyncio.Event()
    set_refresh_event(event)

    assert not event.is_set()
    result = await skill_refresh({})
    assert event.is_set()

    text = result["content"][0]["text"]
    assert len(text) > 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_skills.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Write the implementation**

Create `src/vtuber/tools/skills.py`:

```python
"""Skills MCP tools — create, invoke, update, delete, and refresh agent skills."""

import asyncio
import shutil
from typing import Any

import yaml
from claude_agent_sdk import tool
from mcp.types import ToolAnnotations

from vtuber.config import get_skills_dir

# Injected by daemon on startup
_refresh_event: asyncio.Event | None = None


def set_refresh_event(event: asyncio.Event) -> None:
    """Set the refresh event (called by daemon on startup)."""
    global _refresh_event
    _refresh_event = event


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a SKILL.md file.

    Returns (metadata_dict, body_text).
    """
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}

    body = parts[2].strip()
    return meta, body


def build_skill_summary() -> str:
    """Build a summary of all available skills for system prompt injection.

    Scans ~/.vtuber/skills/*/SKILL.md, extracts name + description
    from YAML frontmatter, returns formatted summary text.
    Returns empty string if no skills exist.
    """
    skills_dir = get_skills_dir()
    if not skills_dir.exists():
        return ""

    entries = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            content = skill_file.read_text(encoding="utf-8")
            meta, _ = _parse_frontmatter(content)
            name = meta.get("name", skill_dir.name)
            desc = meta.get("description", "")
            entries.append(f"- **{name}**: {desc}" if desc else f"- **{name}**")
        except Exception:
            continue

    if not entries:
        return ""

    return (
        "## 可用技能\n\n"
        "使用 skill_invoke 工具调用技能。\n\n"
        + "\n".join(entries)
    )


SKILL_WRITING_GUIDE = """\
Skill directory created at: {path}

Please create a SKILL.md file in this directory with the following structure:

```markdown
---
name: {name}
description: A short description of what this skill does
---

# Skill Title

Your skill instructions go here. This content will be returned
verbatim when the skill is invoked via skill_invoke.

Include any prompts, guidelines, checklists, or templates the
agent should follow when this skill is active.
```

Use the Write tool to create the SKILL.md file, then call skill_refresh
to update the system prompt with the new skill description."""


@tool(
    "skill_invoke",
    "Invoke a skill by name. Returns the full SKILL.md content for the agent to follow.",
    {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Name of the skill to invoke",
            },
            "args": {
                "type": "string",
                "description": "Optional arguments to pass to the skill",
            },
        },
        "required": ["skill"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def skill_invoke(args: dict[str, Any]) -> dict[str, Any]:
    """Invoke a skill and return its full content."""
    skill_name = args["skill"]
    skill_args = args.get("args", "")
    skills_dir = get_skills_dir()
    skill_file = skills_dir / skill_name / "SKILL.md"

    if not skill_file.exists():
        return {
            "content": [{"type": "text", "text": f"Skill '{skill_name}' not found."}]
        }

    content = skill_file.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(content)

    text = body
    if skill_args:
        text = f"Arguments: {skill_args}\n\n{text}"

    return {"content": [{"type": "text", "text": text}]}


@tool(
    "skill_create",
    "Create a new skill directory. Returns the path and instructions for writing the SKILL.md file.",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name for the new skill (used as directory name)",
            },
        },
        "required": ["name"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def skill_create(args: dict[str, Any]) -> dict[str, Any]:
    """Create a new skill directory and return writing instructions."""
    name = args["name"]
    skills_dir = get_skills_dir()
    skill_path = skills_dir / name

    if skill_path.exists():
        return {
            "content": [
                {"type": "text", "text": f"Skill '{name}' already exists at {skill_path}"}
            ]
        }

    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_path.mkdir()

    guide = SKILL_WRITING_GUIDE.format(path=skill_path, name=name)
    return {"content": [{"type": "text", "text": guide}]}


@tool(
    "skill_update",
    "Get the path to a skill's SKILL.md file for editing. Use Read/Edit tools to modify it.",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the skill to update",
            },
        },
        "required": ["name"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def skill_update(args: dict[str, Any]) -> dict[str, Any]:
    """Return the path to a skill file for editing."""
    name = args["name"]
    skills_dir = get_skills_dir()
    skill_file = skills_dir / name / "SKILL.md"

    if not skill_file.exists():
        return {
            "content": [{"type": "text", "text": f"Skill '{name}' not found."}]
        }

    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Skill file path: {skill_file}\n\n"
                    "Use the Read tool to view it, then Edit to modify. "
                    "Call skill_refresh after editing to update the system prompt."
                ),
            }
        ]
    }


@tool(
    "skill_delete",
    "Delete a skill and its directory.",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the skill to delete",
            },
        },
        "required": ["name"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
)
async def skill_delete(args: dict[str, Any]) -> dict[str, Any]:
    """Delete a skill directory."""
    name = args["name"]
    skills_dir = get_skills_dir()
    skill_path = skills_dir / name

    if not skill_path.exists():
        return {
            "content": [{"type": "text", "text": f"Skill '{name}' not found."}]
        }

    shutil.rmtree(skill_path)
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Skill '{name}' deleted. "
                    "Call skill_refresh to update the system prompt."
                ),
            }
        ]
    }


@tool(
    "skill_refresh",
    "Refresh the agent's system prompt with updated skill descriptions. "
    "Call this after creating, updating, or deleting skills.",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def skill_refresh(args: dict[str, Any]) -> dict[str, Any]:
    """Signal the daemon to refresh the agent with updated skills."""
    if _refresh_event is None:
        return {
            "content": [
                {"type": "text", "text": "Error: Refresh not available (daemon not running)."}
            ]
        }

    _refresh_event.set()
    return {
        "content": [
            {
                "type": "text",
                "text": "Skill refresh triggered. The system prompt will be updated after this response completes.",
            }
        ]
    }
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_skills.py -v`
Expected: All 12 tests PASS

**Step 5: Commit**

```bash
git add src/vtuber/tools/skills.py tests/test_skills.py
git commit -m "feat: add skills MCP tools with 5 tools and build_skill_summary"
```

---

### Task 3: Inject skill summary into system prompt via persona.py

**Files:**
- Modify: `src/vtuber/persona.py:82-105`
- Test: `tests/test_persona_markdown.py`

**Step 1: Write the failing test**

Add to `tests/test_persona_markdown.py`:

```python
def test_build_system_prompt_with_skills(tmp_path, monkeypatch):
    """Test that skill summary is injected into system prompt."""
    from vtuber.tools.skills import build_skill_summary

    persona = tmp_path / "persona.md"
    user = tmp_path / "user.md"
    persona.write_text("# Test Persona")
    user.write_text("# Test User")

    # Create a skills dir with a skill
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "test-skill").mkdir()
    (skills_dir / "test-skill" / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill\n---\n\nDo test things.\n"
    )

    monkeypatch.setattr("vtuber.tools.skills.get_skills_dir", lambda: skills_dir)

    result = build_system_prompt(persona, user)
    assert "test-skill" in result
    assert "A test skill" in result
    assert "skill_invoke" in result
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_persona_markdown.py::test_build_system_prompt_with_skills -v`
Expected: FAIL (skill summary not yet injected)

**Step 3: Modify persona.py**

In `src/vtuber/persona.py`, add the import at the top and modify `build_system_prompt`:

Add import after existing imports:
```python
from vtuber.tools.skills import build_skill_summary
```

Modify `build_system_prompt()` — after the `tools_section` and before the `long_term_memory` section, add skill summary. Change the function to:

```python
def build_system_prompt(persona_path: Path, user_path: Path) -> str:
    """Build system prompt from persona.md, user.md, long-term memory, tools, and skills."""
    persona_content = _read_or_default(persona_path, DEFAULT_PERSONA)
    user_content = _read_or_default(user_path, DEFAULT_USER)
    tools_section = TOOLS_SECTION.format(
        user_path=str(get_user_path()),
        long_term_memory_path=str(get_long_term_memory_path()),
        history_path=str(get_history_path()),
    )
    long_term_memory = _read_long_term_memory()
    skill_summary = build_skill_summary()

    parts = [
        persona_content,
        "---",
        user_content,
        "---",
        tools_section,
    ]

    if skill_summary:
        parts.extend(["---", skill_summary])

    if long_term_memory:
        parts.extend(["---", long_term_memory])

    return "\n\n".join(parts)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_persona_markdown.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add src/vtuber/persona.py tests/test_persona_markdown.py
git commit -m "feat: inject skill summary into system prompt"
```

---

### Task 4: Register skill tools in agents.py and add TOOLS_SECTION description

**Files:**
- Modify: `src/vtuber/daemon/agents.py:15-33`
- Modify: `src/vtuber/persona.py` (TOOLS_SECTION)

**Step 1: Modify `create_tools_server()` in agents.py**

Add skill tools to the MCP server registration. Modify the function:

```python
def create_tools_server(
    include_schedule: bool = True,
    refresh_event: asyncio.Event | None = None,
):
    """Create an SDK MCP server with vtuber tools.

    Returns:
        (server, allowed_tool_names) tuple.
    """
    from vtuber.tools.memory import search_sessions, list_sessions, read_session, search_history
    from vtuber.tools.skills import (
        skill_invoke, skill_create, skill_update, skill_delete, skill_refresh,
        set_refresh_event,
    )

    tools = [search_sessions, list_sessions, read_session, search_history]
    allowed = ["search_sessions", "list_sessions", "read_session", "search_history"]

    if include_schedule:
        from vtuber.tools.schedule import schedule_create, schedule_list, schedule_cancel

        tools.extend([schedule_create, schedule_list, schedule_cancel])
        allowed.extend(["schedule_create", "schedule_list", "schedule_cancel"])

    # Skill tools (always included)
    tools.extend([skill_invoke, skill_create, skill_update, skill_delete, skill_refresh])
    allowed.extend(["skill_invoke", "skill_create", "skill_update", "skill_delete", "skill_refresh"])

    if refresh_event is not None:
        set_refresh_event(refresh_event)

    server = create_sdk_mcp_server("vtuber-tools", tools=tools)
    return server, allowed
```

**Step 2: Add skills description to TOOLS_SECTION in persona.py**

In `src/vtuber/persona.py`, add after the `### 日程管理` block and before `## 记忆管理`:

```python
### 技能系统
- **skill_invoke(skill, args?)**: 调用一个已有技能，返回其完整内容供你执行
- **skill_create(name)**: 创建新技能目录，返回路径和 SKILL.md 写作指南
- **skill_update(name)**: 获取技能文件路径，使用 Read/Edit 工具修改
- **skill_delete(name)**: 删除一个技能及其目录
- **skill_refresh()**: 刷新系统提示词以包含最新的技能描述（创建/更新/删除技能后调用）
```

**Step 3: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add src/vtuber/daemon/agents.py src/vtuber/persona.py
git commit -m "feat: register skill tools in MCP server and add TOOLS_SECTION description"
```

---

### Task 5: Wire refresh event + agent resume in server.py

**Files:**
- Modify: `src/vtuber/daemon/server.py:86-116` (start method)
- Modify: `src/vtuber/daemon/server.py:243-271` (_handle_private_message)
- Modify: `src/vtuber/daemon/agents.py:36-80` (create_agent)

**Step 1: Add `refresh_event` to DaemonServer.__init__ and pass to create_tools_server**

In `src/vtuber/daemon/server.py`, modify `__init__`:

```python
def __init__(self, socket_path: Path | None = None):
    # ... existing fields ...
    self._refresh_event = asyncio.Event()
```

In `start()`, pass the event when creating the agent. Change:

```python
self.agent = await create_agent(
    include_schedule=True,
    include_preset_tools=True,
)
```

to:

```python
self.agent = await create_agent(
    include_schedule=True,
    include_preset_tools=True,
    refresh_event=self._refresh_event,
)
```

**Step 2: Add `refresh_event` parameter to `create_agent()` in agents.py**

Modify `create_agent` signature and pass `refresh_event` to `create_tools_server`:

```python
async def create_agent(
    *,
    system_prompt: str | None = None,
    prompt_suffix: str = "",
    include_schedule: bool = False,
    include_mcp_tools: bool = True,
    include_preset_tools: bool = False,
    session_persistence: bool = False,
    refresh_event: asyncio.Event | None = None,
    resume: bool = False,
) -> ClaudeSDKClient:
```

In the body, pass `refresh_event`:

```python
    if include_mcp_tools:
        tools_server, allowed_tools = create_tools_server(
            include_schedule=include_schedule,
            refresh_event=refresh_event,
        )
```

And handle `resume`:

```python
    if resume:
        options_kwargs["resume"] = True
```

**Step 3: Add refresh check after `_handle_private_message`**

In `_handle_private_message`, after the agent response completes, check and handle refresh. Add after the `self._heartbeat.on_message()` line:

```python
    if self._refresh_event.is_set():
        await self._do_refresh()
```

Add the `_do_refresh` method to `DaemonServer`:

```python
async def _do_refresh(self) -> None:
    """Refresh the main agent with updated system prompt (preserving session)."""
    self._refresh_event.clear()
    logger.info("Refreshing agent (skill update)")

    if self.agent:
        try:
            await self.agent.disconnect()
        except Exception:
            pass

    self.agent = await create_agent(
        include_schedule=True,
        include_preset_tools=True,
        refresh_event=self._refresh_event,
        resume=True,
    )
    logger.info("Agent refreshed with updated system prompt")
```

**Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/vtuber/daemon/server.py src/vtuber/daemon/agents.py
git commit -m "feat: wire refresh event and agent resume for skill_refresh"
```

---

### Task 6: Verify full integration

**Files:**
- All modified files

**Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

**Step 2: Verify imports are clean**

Run: `uv run python -c "from vtuber.tools.skills import skill_invoke, skill_create, skill_update, skill_delete, skill_refresh, build_skill_summary; print('All imports OK')"`
Expected: "All imports OK"

**Step 3: Verify SKILL.md parsing works end-to-end**

Run: `uv run python -c "
from pathlib import Path
from vtuber.tools.skills import _parse_frontmatter
text = '---\nname: test\ndescription: A test\n---\n\nBody here.'
meta, body = _parse_frontmatter(text)
assert meta['name'] == 'test'
assert meta['description'] == 'A test'
assert body == 'Body here.'
print('Frontmatter parsing OK')
"`
Expected: "Frontmatter parsing OK"

**Step 4: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "chore: fix any integration issues from skills MCP implementation"
```
