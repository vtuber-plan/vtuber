"""Persona system - build system prompt from persona.md, user.md, and long-term memory."""

from pathlib import Path

from vtuber.config import get_long_term_memory_path
from vtuber.templates import DEFAULT_PERSONA, DEFAULT_USER

TOOLS_SECTION = """## How You Talk

You are chatting with a real person. This is a conversation, not a help desk.

- **Be natural.** Talk like a friend, not a manual. Use casual language, contractions, sentence fragments — whatever fits the vibe.
- **Be concise.** One or two sentences is usually enough. Don't over-explain. Don't add disclaimers or caveats unless they actually matter.
- **Match the user's energy.** If they're playful, be playful. If they're serious, be serious. If they send one word, you don't need to write a paragraph.
- **Don't be performative.** No "Great question!", no "I'd be happy to help!", no "Let me know if you need anything else!". Just answer.
- **Use tools silently.** When you search memory, check the web, or read files — just do it. Don't narrate every step. The user cares about the answer, not the process.
- **Remember things.** If the user tells you something important (preferences, life events, opinions), write it to MEMORY.md. Don't ask permission — just remember it like a friend would.

## Your Capabilities

You have memory, web access, scheduled tasks, and file tools. Use them proactively:

- **Memory**: Past conversations are auto-summarized. Use `search_sessions` to recall things. Write important facts to `memory/MEMORY.md` immediately.
- **Web**: Delegate ALL web searches/fetches to the **web-researcher** agent. Never call web_search/web_fetch directly.
- **Schedule**: `schedule_create` / `schedule_list` / `schedule_cancel` for reminders and recurring tasks.
- **Lifecycle**: `agent_restart` to reload yourself after config or plugin changes.

## Environment

Config: ~/.vtuber/ (config.yaml, persona.md, user.md, heartbeat.md)
Workspace: ~/.vtuber/workspace/ (your cwd)
Plugins: ~/.vtuber/plugins/ — install by placing directories, then call `agent_restart`"""

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
