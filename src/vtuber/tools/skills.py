"""Skills system — user-defined reusable prompt snippets for the agent."""

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

    Expects the format:
        ---
        key: value
        ---
        body text here

    Returns (metadata_dict, body_text).
    """
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    # parts[0] is empty (before first ---), parts[1] is YAML, parts[2] is body
    if len(parts) < 3:
        return {}, text

    try:
        metadata = yaml.safe_load(parts[1])
        if not isinstance(metadata, dict):
            metadata = {}
    except yaml.YAMLError:
        metadata = {}

    body = parts[2].strip()
    return metadata, body


def build_skill_summary() -> str:
    """Scan skills directory and build a summary of available skills.

    Returns a formatted summary string, or empty string if no skills exist.
    """
    skills_dir = get_skills_dir()
    if not skills_dir.exists():
        return ""

    entries = []
    for skill_dir in sorted(skills_dir.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not skill_md.exists():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
            metadata, _body = _parse_frontmatter(text)
            name = metadata.get("name", skill_dir.name)
            description = metadata.get("description", "")
            entries.append(f"- {name}: {description}")
        except OSError:
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

Write a SKILL.md file in this directory with YAML frontmatter and a prompt body.

Format:
```
---
name: {name}
description: A short description of what this skill does
---

(Your prompt / instructions here. This is the content returned when the skill is invoked.)
```

Tips:
- The `name` field is the display name of the skill.
- The `description` field appears in the skill summary list.
- The body after the second `---` is the prompt content returned on invocation.
- You may include structured instructions, examples, or templates in the body.
"""


@tool(
    "skill_invoke",
    "Invoke a skill by name. Returns the skill's prompt content from its SKILL.md body.",
    {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Name of the skill to invoke",
            },
            "args": {
                "type": "string",
                "description": "Optional arguments to pass along with the skill",
            },
        },
        "required": ["skill"],
    },
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def skill_invoke(args: dict[str, Any]) -> dict[str, Any]:
    """Invoke a skill by reading its SKILL.md body content."""
    skill_name = args["skill"]
    skill_args = args.get("args", "")
    skill_md = get_skills_dir() / skill_name / "SKILL.md"

    if not skill_md.exists():
        return {"content": [{"type": "text", "text": f"Skill '{skill_name}' not found."}]}

    text = skill_md.read_text(encoding="utf-8")
    _metadata, body = _parse_frontmatter(text)

    if skill_args:
        body = f"Arguments: {skill_args}\n\n{body}"

    return {"content": [{"type": "text", "text": body}]}


@tool(
    "skill_create",
    "Create a new skill directory. Returns the path and a writing guide for SKILL.md.",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the skill to create (used as directory name)",
            },
        },
        "required": ["name"],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def skill_create(args: dict[str, Any]) -> dict[str, Any]:
    """Create a new skill directory."""
    name = args["name"]
    skill_dir = get_skills_dir() / name

    if skill_dir.exists():
        return {"content": [{"type": "text", "text": f"Skill '{name}' already exists."}]}

    skill_dir.mkdir(parents=True, exist_ok=True)
    guide = SKILL_WRITING_GUIDE.format(path=skill_dir, name=name)

    return {"content": [{"type": "text", "text": guide}]}


@tool(
    "skill_update",
    "Get the SKILL.md path for an existing skill so you can edit it.",
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
    """Return the SKILL.md path for editing."""
    name = args["name"]
    skill_md = get_skills_dir() / name / "SKILL.md"

    if not skill_md.exists():
        return {"content": [{"type": "text", "text": f"Skill '{name}' not found."}]}

    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Skill file path: {skill_md}\n\n"
                    "Use the Read tool to view it, then Edit to modify. "
                    "Call skill_refresh after editing to update the system prompt."
                ),
            }
        ]
    }


@tool(
    "skill_delete",
    "Delete a skill and its entire directory.",
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
    skill_dir = get_skills_dir() / name

    if not skill_dir.exists():
        return {"content": [{"type": "text", "text": f"Skill '{name}' not found."}]}

    shutil.rmtree(skill_dir)
    return {"content": [{"type": "text", "text": f"Deleted skill '{name}'."}]}


@tool(
    "skill_refresh",
    "Refresh the agent's skill list. Call this after creating, updating, or deleting skills.",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
)
async def skill_refresh(args: dict[str, Any]) -> dict[str, Any]:
    """Signal the daemon to refresh the skill list."""
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
