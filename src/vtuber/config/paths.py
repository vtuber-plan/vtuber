"""Configuration directory and file path helpers."""

from __future__ import annotations

from pathlib import Path


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


def get_config_path() -> Path:
    """Get the config.yaml file path."""
    return get_config_dir() / "config.yaml"


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


def get_plugins_dir() -> Path:
    """Get the plugins directory path (~/.vtuber/plugins)."""
    return get_config_dir() / "plugins"


def ensure_plugins_dir() -> Path:
    """Ensure the plugins directory exists and return its path."""
    plugins_dir = get_plugins_dir()
    plugins_dir.mkdir(parents=True, exist_ok=True)
    return plugins_dir


def get_log_path() -> Path:
    """Get the daemon log file path."""
    return get_config_dir() / "daemon.log"
