"""Configuration directory, file paths, and user settings."""

from pathlib import Path

from typing import Any

import yaml
from pydantic import BaseModel, Field


# ── User configuration model ────────────────────────────────────────


class ProviderConfig(BaseModel):
    """Per-provider configuration."""

    owner_id: str = Field(
        default="",
        description="Platform-specific user ID of the agent's owner",
    )


class VTuberConfig(BaseModel):
    """User-configurable settings loaded from ~/.vtuber/config.yaml."""

    workspace: str = Field(
        default="~/.vtuber/workspace",
        description="Agent working directory",
    )
    heartbeat_interval: int = Field(
        default=5,
        ge=1,
        description="Minutes between heartbeat checks",
    )
    cli_path: str = Field(
        default="claude",
        description="Path to Claude CLI binary",
    )
    log_level: str = Field(
        default="INFO",
        pattern="^(DEBUG|INFO|WARNING|ERROR)$",
        description="Logging level",
    )
    response_timeout: int = Field(
        default=300,
        ge=10,
        description="CLI response timeout in seconds",
    )
    providers: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-provider settings (e.g., discord.owner_id)",
    )

    def get_provider_config(self, provider_type: str) -> ProviderConfig:
        """Get config for a specific provider type."""
        raw = self.providers.get(provider_type, {})
        return ProviderConfig(**raw)


_config: VTuberConfig | None = None


def get_config_path() -> Path:
    """Get the config.yaml file path."""
    return get_config_dir() / "config.yaml"


def load_config() -> VTuberConfig:
    """Load config from ~/.vtuber/config.yaml, falling back to defaults."""
    config_path = get_config_path()
    if config_path.exists():
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return VTuberConfig(**raw)
        except Exception:
            pass
    return VTuberConfig()


def get_config() -> VTuberConfig:
    """Get the singleton config instance (lazy-loaded)."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Reset the cached config (forces reload on next get_config() call)."""
    global _config
    _config = None


# ── Config directory and file paths ─────────────────────────────────


def get_config_dir() -> Path:
    """Get the vtuber configuration directory path."""
    return Path.home() / ".vtuber"


def ensure_config_dir() -> Path:
    """Ensure the configuration directory exists and return its path."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    memory_dir = get_memory_dir()
    memory_dir.mkdir(exist_ok=True)

    return config_dir


def get_workspace_dir() -> Path:
    """Get the agent workspace directory path (resolved from config)."""
    return Path(get_config().workspace).expanduser().resolve()


def ensure_workspace_dir() -> Path:
    """Ensure the workspace directory exists and return its path."""
    workspace = get_workspace_dir()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


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


def get_sessions_dir() -> Path:
    """Get the session logs directory path."""
    return get_config_dir() / "memory" / "sessions"


def ensure_sessions_dir() -> Path:
    """Ensure the sessions directory exists and return its path."""
    sessions_dir = get_sessions_dir()
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def get_memory_dir() -> Path:
    """Get the memory directory path."""
    return get_config_dir() / "memory"


def get_long_term_memory_path() -> Path:
    """Get the long-term memory markdown file path."""
    return get_memory_dir() / "MEMORY.md"


def get_history_path() -> Path:
    """Get the append-only history log file path."""
    return get_memory_dir() / "HISTORY.md"


def get_consolidation_state_path() -> Path:
    """Get the consolidation state file path."""
    return get_config_dir() / "consolidation_state.json"


def get_log_path() -> Path:
    """Get the daemon log file path."""
    return get_config_dir() / "daemon.log"
