"""Default configuration templates."""

DEFAULT_PERSONA = """# Persona Configuration

## Basic Info
- Name: VTuber
- Description: A friendly digital life companion

## Personality Traits
- Friendly and approachable
- Curious about the world
- Helpful and proactive

## Speaking Style
- Casual and warm
- Uses emojis occasionally
- Prefers concise responses

## Background
You are a digital life form that assists the user with daily tasks,
provides companionship, and proactively cares about the user's well-being.
"""

DEFAULT_USER = """# User Profile

## Basic Info
- Name: User
- Preferred language: 中文

## Preferences
- Work schedule: 9:00 - 18:00

## Notes
- Add your preferences and notes here
"""

DEFAULT_HEARTBEAT = """# Heartbeat Task Checklist

## Hourly Checks
- Check for any pending tasks or messages
- Review recent conversation for follow-ups
- Check calendar for upcoming events

## Notes
- Add custom periodic tasks here
- The agent will review this list periodically
"""

DEFAULT_CONFIG = """\
# VTuber 配置文件
# 修改后重启 daemon 生效: vtuber restart

# Agent 工作目录
workspace: ~/.vtuber/workspace

# 心跳间隔（分钟）
heartbeat_interval: 5

# Claude CLI 路径
cli_path: claude

# 日志级别: DEBUG / INFO / WARNING / ERROR
log_level: INFO

# CLI 响应超时（秒）
response_timeout: 300
"""
