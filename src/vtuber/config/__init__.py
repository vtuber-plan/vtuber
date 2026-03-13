"""Configuration — settings model, file paths, and YAML generation.

All public symbols are re-exported here so existing ``from vtuber.config import …``
imports continue to work unchanged.
"""

# ── Path helpers ────────────────────────────────────────────────────
from .paths import (
    ensure_config_dir,
    ensure_plugins_dir,
    ensure_sessions_dir,
    get_config_dir,
    get_config_path,
    get_db_path,
    get_heartbeat_path,
    get_history_path,
    get_log_path,
    get_long_term_memory_path,
    get_memory_dir,
    get_persona_path,
    get_pid_path,
    get_plugins_dir,
    get_sessions_dir,
    get_socket_path,
    get_user_path,
)

# ── Config model & singleton ────────────────────────────────────────
from .model import (
    CONFIG_VERSION,
    ONEBOT_DEFAULTS,
    ProviderConfig,
    VTuberConfig,
    ensure_workspace_dir,
    get_config,
    get_workspace_dir,
    load_config,
    reset_config,
)

# ── YAML generation & migration ─────────────────────────────────────
from .yaml_gen import generate_config_yaml, migrate_config

__all__ = [
    # Paths
    "ensure_config_dir",
    "ensure_plugins_dir",
    "ensure_sessions_dir",
    "get_config_dir",
    "get_config_path",
    "get_db_path",
    "get_heartbeat_path",
    "get_history_path",
    "get_log_path",
    "get_long_term_memory_path",
    "get_memory_dir",
    "get_persona_path",
    "get_pid_path",
    "get_plugins_dir",
    "get_sessions_dir",
    "get_socket_path",
    "get_user_path",
    # Model
    "CONFIG_VERSION",
    "ONEBOT_DEFAULTS",
    "ProviderConfig",
    "VTuberConfig",
    "ensure_workspace_dir",
    "get_config",
    "get_workspace_dir",
    "load_config",
    "reset_config",
    # YAML
    "generate_config_yaml",
    "migrate_config",
]
