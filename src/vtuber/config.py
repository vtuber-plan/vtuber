"""Configuration directory, file paths, and user settings."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


# ── Schema version ──────────────────────────────────────────────────

CONFIG_VERSION = 3

# ── Provider example structures ─────────────────────────────────────
# Used by generate_config_yaml() to populate the providers section with
# working defaults for each implemented provider.

_ONEBOT_DEFAULTS: dict[str, Any] = {
    "ws_url": "ws://127.0.0.1:6700",
    "access_token": "",
    "owner_id": "",
    "bot_names": [],
    "group_batch_size": 0,
    "stream_intermediate": False,
    "user_whitelist": [],
    "group_whitelist": [],
}


# ── User configuration model ────────────────────────────────────────


class ProviderConfig(BaseModel):
    """Per-provider configuration."""

    owner_id: str = Field(
        default="",
        description="Platform-specific user ID of the agent's owner",
    )


class VTuberConfig(BaseModel):
    """User-configurable settings loaded from ~/.vtuber/config.yaml."""

    config_version: int = Field(
        default=0,
        description="配置版本号（升级时自动迁移，请勿手动修改）",
    )
    workspace: str = Field(
        default="~/.vtuber/workspace",
        description="Agent 工作目录",
    )
    heartbeat_interval: int = Field(
        default=30,
        ge=1,
        description="心跳间隔（分钟）",
    )
    cli_path: str = Field(
        default="ripperdoc",
        description="Claude CLI 路径",
    )
    log_level: str = Field(
        default="INFO",
        pattern="^(DEBUG|INFO|WARNING|ERROR)$",
        description="日志级别: DEBUG / INFO / WARNING / ERROR",
    )
    response_timeout: int = Field(
        default=300,
        ge=10,
        description="CLI 响应超时（秒）",
    )
    allowed_write_dirs: list[str] = Field(
        default_factory=lambda: ["~/.vtuber"],
        description="Agent 允许写入的目录列表",
    )
    max_agents: int = Field(
        default=10,
        ge=1,
        description="最大并发 Agent 数（LRU 淘汰）",
    )
    query_timeout: int = Field(
        default=30,
        ge=1,
        description="Agent query 提交超时（秒）",
    )
    idle_timeout: int = Field(
        default=1200,
        ge=10,
        description="Agent 响应流空闲超时（秒）",
    )
    consolidation_threshold: int = Field(
        default=50,
        ge=1,
        description="触发自动消息合并的消息条数阈值",
    )
    consolidation_keep_count: int = Field(
        default=25,
        ge=1,
        description="消息合并后保留的最近消息条数",
    )
    group_context_limit: int = Field(
        default=20,
        ge=1,
        description="群聊上下文保留的最近消息条数",
    )
    web_timeout: int = Field(
        default=30,
        ge=1,
        description="Web 工具 HTTP 请求超时（秒）",
    )
    providers: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Provider 配置（按平台名分区）",
    )
    tavily_api_key: str = Field(
        default="",
        description="Tavily API key（用于 web_search 工具，从 https://tavily.com 获取）",
    )

    def get_provider_config(self, provider_type: str) -> ProviderConfig:
        """Get config for a specific provider type."""
        raw = self.providers.get(provider_type, {})
        return ProviderConfig(**raw)


_config: VTuberConfig | None = None


# ── Config YAML generation ──────────────────────────────────────────


def _build_commented_map(config: VTuberConfig | None = None) -> CommentedMap:
    """Build a CommentedMap from VTuberConfig with field descriptions as comments."""
    if config is None:
        config = VTuberConfig(config_version=CONFIG_VERSION)

    cm = CommentedMap()

    for i, (name, field_info) in enumerate(VTuberConfig.model_fields.items()):
        value = getattr(config, name)

        # Convert lists to CommentedSeq for proper formatting
        if isinstance(value, list):
            seq = CommentedSeq(value)
            value = seq

        # Special handling for providers: populate with implemented provider defaults
        if name == "providers":
            value = _build_providers_map(config.providers)

        cm[name] = value

        # Add field description as a comment above the key
        comment = field_info.description or ""
        if comment:
            cm.yaml_set_comment_before_after_key(name, before=comment, indent=0)

    return cm


def _build_providers_map(user_providers: dict[str, dict[str, Any]]) -> CommentedMap:
    """Build a CommentedMap for the providers section with implemented defaults."""
    pm = CommentedMap()

    # OneBot provider
    onebot_cfg = user_providers.get("onebot", {})
    onebot = CommentedMap()
    for key, default in _ONEBOT_DEFAULTS.items():
        val = onebot_cfg.get(key, default)
        if isinstance(val, list):
            val = CommentedSeq(val)
        onebot[key] = val
    pm["onebot"] = onebot

    # Preserve any other user-defined providers
    for provider_name, provider_cfg in user_providers.items():
        if provider_name not in pm:
            pm[provider_name] = provider_cfg

    return pm


def generate_config_yaml(config: VTuberConfig | None = None) -> str:
    """Generate a complete, commented config.yaml string from the Pydantic model."""
    ry = YAML()
    ry.default_flow_style = False
    ry.allow_unicode = True

    cm = _build_commented_map(config)

    # Add file-level header
    cm.yaml_set_start_comment(
        "VTuber 配置文件\n修改后重启 daemon 生效: vtuber restart"
    )

    buf = io.StringIO()
    ry.dump(cm, buf)
    return buf.getvalue()


# ── Config loading ──────────────────────────────────────────────────


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


# ── Config migration (comment-preserving) ───────────────────────────


def migrate_config() -> None:
    """Migrate config.yaml to the latest version, adding missing fields.

    Uses ruamel.yaml round-trip loading/dumping to preserve user comments
    and formatting.
    """
    config_path = get_config_path()
    if not config_path.exists():
        return

    ry = YAML()
    ry.preserve_quotes = True

    raw_text = config_path.read_text(encoding="utf-8")
    user_data = ry.load(raw_text)
    if not isinstance(user_data, CommentedMap):
        user_data = CommentedMap()

    user_version = user_data.get("config_version", 0)
    if user_version >= CONFIG_VERSION:
        return

    # Generate the reference map with all current fields and comments
    defaults_cm = _build_commented_map()

    # Insert missing keys at the correct position
    default_keys = list(defaults_cm.keys())
    for idx, key in enumerate(default_keys):
        if key not in user_data:
            # Find insertion point: after the previous key that exists, or at start
            insert_pos = 0
            for prev_key in reversed(default_keys[:idx]):
                if prev_key in user_data:
                    # Position after this existing key
                    existing_keys = list(user_data.keys())
                    insert_pos = existing_keys.index(prev_key) + 1
                    break

            user_data.insert(insert_pos, key, defaults_cm[key])

            # Copy the comment from the defaults
            comment = VTuberConfig.model_fields[key].description
            if comment:
                user_data.yaml_set_comment_before_after_key(
                    key, before=comment, indent=0
                )

    user_data["config_version"] = CONFIG_VERSION

    buf = io.StringIO()
    ry.dump(user_data, buf)
    config_path.write_text(buf.getvalue(), encoding="utf-8")

    reset_config()


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
