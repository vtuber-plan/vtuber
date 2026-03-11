"""Default configuration templates."""

CONFIG_VERSION = 2

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

DEFAULT_HEARTBEAT = """# Heartbeat Tasks

This file is checked every 30 minutes by your vtuber agent.
Add tasks below that you want the agent to work on periodically.

If this file has no tasks (only headers and comments), the agent will skip the heartbeat.

## Active Tasks

<!-- Add your periodic tasks below this line -->


## Completed

<!-- Move completed tasks here or delete them -->

"""

DEFAULT_CONFIG = """\
# VTuber 配置文件
# 修改后重启 daemon 生效: vtuber restart

# 配置版本号（升级时自动迁移，请勿手动修改）
config_version: 2

# Agent 工作目录
workspace: ~/.vtuber/workspace

# 心跳间隔（分钟）
heartbeat_interval: 30

# Claude CLI 路径
cli_path: ripperdoc

# 日志级别: DEBUG / INFO / WARNING / ERROR
log_level: INFO

# CLI 响应超时（秒）
response_timeout: 300

# Agent 允许写入的目录列表
# 使用 Edit / Write / MultiEdit 工具时只有这些目录下的文件会被放行
allowed_write_dirs:
  - ~/.vtuber

# Provider 配置（按平台名分区，配置 owner_id 以识别主人）
# providers:
#   onebot:
#     ws_url: "ws://127.0.0.1:6700"  # OneBot 正向 WebSocket 地址
#     access_token: ""                 # 可选，OneBot access_token
#     owner_id: "1134505018"            # 主人的 QQ 号
#     bot_names:                       # 机器人名字（群聊中提及时触发回复）
#       - "小助手"
#     group_batch_size: 0              # 每累积 N 条群消息自动触发（0 = 仅 @/提名触发）
#     stream_intermediate: false       # 是否输出中间过程（默认只发送最终结果）
#     user_whitelist:                  # 私聊白名单（留空则允许所有人，owner 始终放行）
#       - "1134505018"
#     group_whitelist:                 # 群聊白名单（留空则允许所有群）
#       - "673521165"
#   discord:
#     owner_id: "123456789012345678"
#   telegram:
#     owner_id: "112233"

# Tavily API key（用于 web_search 工具，从 https://tavily.com 获取）
# tavily_api_key: "tvly-xxxxx"
"""
