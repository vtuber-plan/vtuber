"""Persona system - build system prompt from persona.md, user.md, and long-term memory."""

from pathlib import Path

from vtuber.config import get_long_term_memory_path
from vtuber.templates import DEFAULT_PERSONA, DEFAULT_USER

TOOLS_SECTION = """## Memory System

- `memory/MEMORY.md` — Long-term facts (preferences, context). Always in your context.
- Past conversations are automatically summarized and stored. Use `search_sessions` to search them.

## Searching Past Memories

Use the `search_sessions` tool:
- `source="summary"` (default) — Search consolidated summaries. Fast, good for most recall.
- `source="detailed"` — Search raw conversation logs. Use when you need exact quotes or full context.

## When to Update MEMORY.md

Write important facts immediately using Tools:
- User preferences
- Project context
- Important relationships

## Auto-consolidation

Old conversations are automatically summarized. You don't manage this.

## Web Research

When you need to search the web or fetch web pages, ALWAYS delegate to the **web-researcher** agent.
Do NOT call web_search or web_fetch tools directly. Instead, use the Agent tool:
- Describe what you need to find clearly
- The web-researcher will search/fetch and return a concise summary
- This keeps your context clean and focused

## Environment

You are running as a VTuber digital life.

### Key Paths
- Config directory: ~/.vtuber/
- Config file: ~/.vtuber/config.yaml — main settings (restart daemon after changes)
- Persona: ~/.vtuber/persona.md — your personality definition
- User profile: ~/.vtuber/user.md — info about the user
- Workspace: ~/.vtuber/workspace/ — your working directory (cwd)
- Plugins: ~/.vtuber/plugins/ — installed plugins directory
- Memory: ~/.vtuber/memory/MEMORY.md — long-term memory
- History: ~/.vtuber/memory/HISTORY.md — conversation history summaries
- Heartbeat: ~/.vtuber/heartbeat.md — periodic tasks definition

### Plugins
Plugins extend your capabilities with skills, commands, agents, and hooks.
- Installed plugins live in ~/.vtuber/plugins/<plugin-name>/
- Each plugin has a `.claude-plugin/plugin.json` manifest
- To install a plugin: clone/copy/move the plugin directory into ~/.vtuber/plugins/
- To remove a plugin: delete its directory from ~/.vtuber/plugins/
- Changes take effect after you call `agent_restart` to restart yourself

### Schedule Tools
- `schedule_create` — create one-time or recurring scheduled tasks
- `schedule_list` — list all scheduled tasks
- `schedule_cancel` — cancel a scheduled task by ID

### Lifecycle Tools
- `agent_restart` — restart yourself (clears conversation context, reloads config and plugins). Use after installing/removing plugins or when asked to restart."""

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
    tools_section = TOOLS_SECTION
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
