"""Persona system - build system prompt from persona.md and user.md."""

from pathlib import Path

from vtuber.templates import DEFAULT_PERSONA, DEFAULT_USER

TOOLS_SECTION = """## 内置能力

你拥有以下工具：
- **记忆** (memorize/recall/forget): 你可以记住和回忆跨对话的持久记忆
- **日程** (schedule_create/schedule_list/schedule_cancel): 你可以创建定时提醒
- **心跳** (heartbeat): 你可以记录你的活动状态

请自然地使用这些工具来增强你的交互体验。"""


def _read_or_default(path: Path, default: str) -> str:
    """Read file content, falling back to default if missing or empty."""
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return default.strip()


def build_system_prompt(persona_path: Path, user_path: Path) -> str:
    """Build system prompt from persona.md and user.md files."""
    persona_content = _read_or_default(persona_path, DEFAULT_PERSONA)
    user_content = _read_or_default(user_path, DEFAULT_USER)

    return (
        f"{persona_content}\n\n"
        f"---\n\n"
        f"{user_content}\n\n"
        f"---\n\n"
        f"{TOOLS_SECTION}"
    )
