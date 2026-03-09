"""Persona system - build system prompt from persona.md, user.md, and long-term memory."""

from pathlib import Path

from vtuber.config import get_long_term_memory_path, get_history_path, get_user_path
from vtuber.templates import DEFAULT_PERSONA, DEFAULT_USER

TOOLS_SECTION = """## Memory System

- `memory/MEMORY.md` — Long-term facts (preferences, context). Always in your context.
- `memory/HISTORY.md` — Append-only event log. NOT loaded. Search with grep.

## Search Past Events

Use Bash tool:
```bash
grep -i "keyword" ~/.vtuber/memory/HISTORY.md
```

## When to Update MEMORY.md

Write important facts immediately using Tools:
- User preferences
- Project context
- Important relationships

## Auto-consolidation

Old conversations are automatically summarized to HISTORY.md and MEMORY.md. You don't manage this."""

LONG_TERM_MEMORY_HEADER = """## Long-term Memory

The following is your long-term memory from MEMORY.md:

"""


def _read_or_default(path: Path, default: str) -> str:
    """Read file content, falling back to default if missing or empty."""
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return default.strip()


def _read_long_term_memory() -> str:
    """Read long-term memory file, return empty string if not exists."""
    memory_path = get_long_term_memory_path()
    if memory_path.exists():
        content = memory_path.read_text(encoding="utf-8").strip()
        if content:
            return LONG_TERM_MEMORY_HEADER + content
    return ""


def build_system_prompt(persona_path: Path, user_path: Path) -> str:
    """Build system prompt from persona.md, user.md, long-term memory, and tools."""
    persona_content = _read_or_default(persona_path, DEFAULT_PERSONA)
    user_content = _read_or_default(user_path, DEFAULT_USER)
    tools_section = TOOLS_SECTION.format(
        user_path=str(get_user_path()),
        long_term_memory_path=str(get_long_term_memory_path()),
        history_path=str(get_history_path()),
    )
    long_term_memory = _read_long_term_memory()

    parts = [
        persona_content,
        "---",
        user_content,
        "---",
        tools_section,
    ]

    if long_term_memory:
        parts.extend(["---", long_term_memory])

    return "\n\n".join(parts)
