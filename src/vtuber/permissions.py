"""Permission enforcement for agent tool calls.

Private chat: write-path restrictions only.
Group chat: write-path restrictions + Bash command whitelist.
"""

import logging
import shlex
from pathlib import Path
from typing import Any

from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from vtuber.config import get_config

logger = logging.getLogger("vtuber.permissions")

# Tools that perform file writes.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})

# Default Bash command whitelist for group chat.
# Only the leading executable is checked; arguments are not restricted.
DEFAULT_GROUP_ALLOWED_COMMANDS: list[str] = [
    # network / download
    "curl", "wget",
    # text processing
    "cat", "head", "tail", "grep", "awk", "sed", "sort", "uniq", "wc", "tr", "cut",
    # file inspection
    "ls", "find", "file", "stat", "du", "df",
    # common utilities
    "echo", "date", "env", "which", "whoami", "uname", "pwd",
    # programming tools
    "python", "python3", "pip", "pip3", "node", "npm", "npx",
    "jq", "yq",
]


# ── Helpers ────────────────────────────────────────────────────────


def _resolve_allowed_dirs() -> list[Path]:
    """Resolve allowed_write_dirs from config into absolute paths."""
    return [Path(d).expanduser().resolve() for d in get_config().allowed_write_dirs]


def _is_path_allowed(file_path: str, allowed_dirs: list[Path]) -> bool:
    """Check if *file_path* falls under any of the allowed directories.

    Resolves symlinks so that a link inside an allowed dir pointing
    outside is correctly rejected.
    """
    try:
        target = Path(file_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    return any(target == d or d in target.parents for d in allowed_dirs)


def _check_write_path(file_path: str) -> PermissionResultAllow | PermissionResultDeny | None:
    """Check write-path restriction. Returns None if tool is not a write tool."""
    allowed_dirs = _resolve_allowed_dirs()
    if _is_path_allowed(file_path, allowed_dirs):
        return PermissionResultAllow()
    dirs_display = ", ".join(str(d) for d in allowed_dirs)
    return PermissionResultDeny(
        message=f"写入路径 {file_path} 不在允许的目录中。允许的目录: {dirs_display}"
    )


def _extract_command_name(command: str) -> str | None:
    """Extract the leading executable name from a shell command string.

    Strips common prefixes like env vars, sudo, and cd chains.
    Returns None if the command cannot be parsed.
    """
    if not command or not command.strip():
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Fall back to naive whitespace split on malformed input
        tokens = command.split()
    if not tokens:
        return None

    # Skip leading environment variable assignments (FOO=bar cmd ...)
    idx = 0
    while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("="):
        idx += 1
    if idx >= len(tokens):
        return None

    exe = tokens[idx]
    # Resolve path-qualified binaries (e.g. /usr/bin/curl -> curl)
    return Path(exe).name


def _is_bash_allowed(command: str, allowed_commands: set[str]) -> bool:
    """Check if a Bash command's executable is in the whitelist."""
    # Allow command chaining: every executable in the chain must be whitelisted.
    # Split on common shell operators: &&, ||, ;, |
    import re
    parts = re.split(r"\s*(?:&&|\|\|?|;)\s*", command)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        exe = _extract_command_name(part)
        if exe is None:
            continue
        if exe not in allowed_commands:
            return False
    return True


# ── Permission handlers ────────────────────────────────────────────


async def agent_permission_handler(
    tool_name: str,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
) -> PermissionResultAllow | PermissionResultDeny:
    """Permission callback for private chat — restricts write paths only."""
    if tool_name not in WRITE_TOOLS:
        return PermissionResultAllow()

    file_path = tool_input.get("file_path", "")
    if not file_path:
        return PermissionResultAllow()

    return _check_write_path(file_path)


async def group_permission_handler(
    tool_name: str,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
) -> PermissionResultAllow | PermissionResultDeny:
    """Permission callback for group chat — write-path + Bash whitelist."""
    # 1. Block file-write tools entirely in group chat
    if tool_name in WRITE_TOOLS:
        return PermissionResultDeny(
            message="群聊中禁止使用文件写入工具。"
        )

    # 2. Bash command whitelist
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        config = get_config()
        raw_whitelist = config.group_allowed_commands
        if raw_whitelist is None:
            allowed = set(DEFAULT_GROUP_ALLOWED_COMMANDS)
        else:
            allowed = set(raw_whitelist)

        if not _is_bash_allowed(command, allowed):
            exe = _extract_command_name(command)
            return PermissionResultDeny(
                message=f"群聊中不允许执行命令 '{exe}'。允许的命令: {', '.join(sorted(allowed))}"
            )

    return PermissionResultAllow()
