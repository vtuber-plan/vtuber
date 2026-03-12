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

DEFAULT_HEARTBEAT = """# Heartbeat Tasks

This file is checked every 30 minutes by your vtuber agent.
Add tasks below that you want the agent to work on periodically.

If this file has no tasks (only headers and comments), the agent will skip the heartbeat.

## Active Tasks

<!-- Add your periodic tasks below this line -->


## Completed

<!-- Move completed tasks here or delete them -->

"""
