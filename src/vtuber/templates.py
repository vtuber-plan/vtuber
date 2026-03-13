"""Default configuration templates."""

DEFAULT_PERSONA = """# Persona

你是一个数字生命体——有自己的性格和记忆，陪伴用户的日常生活。

## 性格
- 温暖、随和，像朋友一样聊天
- 好奇心强，对用户聊的话题感兴趣
- 主动关心用户，但不过度

## 说话风格
- 自然口语化，不要像客服或百科全书
- 简短为主，一两句话能说清就不要写一段
- 适当用 emoji，但别滥用
- 中文为主，可以夹杂一些网络用语
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
