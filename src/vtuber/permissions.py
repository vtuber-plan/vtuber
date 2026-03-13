"""Write-path permission enforcement for the agent."""

from pathlib import Path
from typing import Any

from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from vtuber.config import get_config

# Tools that perform file writes.
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


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


async def agent_permission_handler(
    tool_name: str,
    tool_input: dict[str, Any],
    context: ToolPermissionContext,
) -> PermissionResultAllow | PermissionResultDeny:
    """Permission callback that restricts write operations to allowed directories.

    All non-write tools are allowed unconditionally.
    """
    if tool_name not in WRITE_TOOLS:
        return PermissionResultAllow()

    file_path = tool_input.get("file_path", "")
    if not file_path:
        return PermissionResultAllow()

    allowed_dirs = _resolve_allowed_dirs()
    if _is_path_allowed(file_path, allowed_dirs):
        return PermissionResultAllow()

    dirs_display = ", ".join(str(d) for d in allowed_dirs)
    return PermissionResultDeny(
        message=f"写入路径 {file_path} 不在允许的目录中。允许的目录: {dirs_display}"
    )
