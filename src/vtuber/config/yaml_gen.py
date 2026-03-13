"""Config YAML generation and comment-preserving migration."""

from __future__ import annotations

import io
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .model import CONFIG_VERSION, ONEBOT_DEFAULTS, VTuberConfig
from .paths import get_config_path


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
    for key, default in ONEBOT_DEFAULTS.items():
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


# ── Config migration (comment-preserving) ───────────────────────────


def migrate_config() -> None:
    """Migrate config.yaml to the latest version, adding missing fields.

    Uses ruamel.yaml round-trip loading/dumping to preserve user comments
    and formatting.
    """
    from .model import reset_config

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
