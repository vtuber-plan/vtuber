"""User configuration model and singleton access."""

from __future__ import annotations

import logging
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .paths import get_config_path

logger = logging.getLogger("vtuber.config")


# ── Schema version ──────────────────────────────────────────────────

CONFIG_VERSION = 4

# ── Provider example structures ─────────────────────────────────────

ONEBOT_DEFAULTS: dict[str, Any] = {
    "ws_url": "ws://127.0.0.1:6700",
    "access_token": "",
    "owner_id": "",
    "bot_names": [],
    "group_reply_delay": 120,
    "stream_intermediate": False,
    "user_whitelist": [],
    "group_whitelist": [],
    "text2img_url": "",
    "long_text_threshold": 300,
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


# ── Config loading ──────────────────────────────────────────────────


def load_config() -> VTuberConfig:
    """Load config from ~/.vtuber/config.yaml, falling back to defaults."""
    config_path = get_config_path()
    if config_path.exists():
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return VTuberConfig(**raw)
        except Exception as e:
            logger.warning("Failed to load config from %s: %s — using defaults", config_path, e)
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


# ── Workspace helpers ───────────────────────────────────────────────


def get_workspace_dir():
    """Get the agent workspace directory path (resolved from config)."""
    from pathlib import Path

    return Path(get_config().workspace).expanduser().resolve()


def ensure_workspace_dir():
    """Ensure the workspace directory exists and return its path."""
    workspace = get_workspace_dir()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace
