# Vtuber Daemon Architecture Design

**Date**: 2026-03-05
**Status**: Approved

## Overview

Refactor vtuber from a single-process CLI application to a daemon + client architecture, enabling persistent agent context, background task execution, and multi-client access.

## Goals

- **Persistent agent context**: Main agent maintains conversation history across client sessions
- **Background tasks**: Scheduled tasks execute even when no client is connected
- **Proactive agent**: Heartbeat mechanism enables periodic autonomous actions
- **Configuration from files**: Load persona and user info from markdown files
- **Interactive onboarding**: Guide new users through initial setup via conversational agent

## Architecture

```
┌─────────────────────────────────────────────┐
│           Vtuber Daemon                      │
├─────────────────────────────────────────────┤
│  Unix Socket Server (daemon.sock)           │
│  - Accept client connections                │
│  - Route messages (user input → agent)      │
│  - Broadcast responses (agent → clients)    │
├─────────────────────────────────────────────┤
│  ClaudeSDKClient (Main Agent Session)       │
│  - Persistent session with context          │
│  - Receive user messages + heartbeat        │
│  - Stream responses → broadcast to clients  │
├─────────────────────────────────────────────┤
│  APScheduler                                 │
│  - Persist to vtuber.db                     │
│  - Trigger scheduled tasks at exact time    │
│  - Execute via temporary subagent           │
├─────────────────────────────────────────────┤
│  Heartbeat Timer (asyncio)                  │
│  - Trigger every N minutes (default: 60)   │
│  - Notify main agent to check tasks        │
└─────────────────────────────────────────────┘
```

## Directory Structure

```
~/.vtuber/
├── persona.md              # Agent personality configuration
├── user.md                 # User profile information
├── heartbeat.md            # Heartbeat task checklist
├── memory/
│   └── global.json         # Persistent key-value memory
├── vtuber.db               # APScheduler SQLite storage
├── daemon.sock             # Unix Domain Socket
└── daemon.pid              # Daemon process ID

src/vtuber/
├── daemon/
│   ├── __init__.py
│   ├── server.py           # Unix socket server + message routing
│   ├── protocol.py         # JSON message protocol
│   └── scheduler.py        # APScheduler integration + task execution
├── client/
│   ├── __init__.py
│   └── cli.py              # CLI client implementation
├── tools/
│   ├── schedule.py         # Schedule tools (create/list/cancel)
│   ├── memory.py           # Memory tools (memorize/recall/forget)
│   └── __init__.py
├── persona.py              # Load persona from ~/.vtuber/persona.md
└── main.py                 # Command routing (start/stop/status/chat)
```

## Components

### 1. Communication Protocol

**Transport**: Unix Domain Socket at `~/.vtuber/daemon.sock`
**Format**: JSON messages, newline-delimited

**Request Messages** (Client → Daemon):
```json
{"type": "user_message", "content": "Hello"}
{"type": "ping"}
```

**Response Messages** (Daemon → Client):
```json
// Streaming response - intermediate chunk
{"type": "assistant_message", "stream_id": "abc123", "index": 0, "content": "Hello", "is_final": false}

// Streaming response - final chunk
{"type": "assistant_message", "stream_id": "abc123", "index": 3, "content": "!", "is_final": true}

// Complete message (non-streaming)
{"type": "system_message", "content": "[HEARTBEAT] Please check tasks...", "is_final": true}

// Control messages
{"type": "pong", "is_final": true}
{"type": "error", "message": "...", "is_final": true}
```

**Streaming Protocol**:
- `stream_id`: Shared by all chunks in same response
- `index`: Chunk order (0-based)
- `is_final`: `true` indicates end of stream
- Clients group by `stream_id`, sort by `index`, render on `is_final`

### 2. Configuration Files

**~/.vtuber/persona.md**:
```markdown
# Persona Configuration

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
```

**~/.vtuber/user.md**:
```markdown
# User Profile

## Basic Info
- Name: User
- Preferred language: 中文

## Preferences
- Work schedule: 9:00 - 18:00
- Interests: Programming, AI, Music

## Notes
- Prefers morning briefs at 9:00
- Likes to receive weekly summaries on Friday
```

**~/.vtuber/heartbeat.md**:
```markdown
# Heartbeat Task Checklist

## Hourly Checks
- Check email for important messages
- Review calendar for upcoming events
- Review recent conversation history for follow-ups

## User-defined Tasks
- Check website updates
- ...
```

### 3. Onboarding Flow

When `persona.md` or `user.md` doesn't exist:

1. **Detect missing config**: Daemon checks on startup
2. **Launch onboarding agent**: Dedicated ClaudeSDKClient session
3. **Ask about agent persona** (open-ended):
   ```
   "Hello! I'm your digital life assistant. Before we start, please tell me:
    What would you like to call me? What personality traits or settings should I have?
    You can describe briefly, in detail, or say 'help me generate one'."
   ```
4. **User responds** (open-ended):
   - "Name: Xiaoxue, a lively and cute anime girl"
   - "Help me generate a gentle and caring assistant"
   - [Long detailed description...]
5. **Generate persona.md**: Onboarding agent creates configuration
6. **Show to user for confirmation**:
   ```
   "Here's what I generated for you:
    ---
    [persona.md content]
    ---
    Does this look good? Any changes needed?"
   ```
7. **User confirms or requests modifications**
8. **Ask about user info** (open-ended):
   ```
   "Next, tell me some basic information about yourself.
    For example: what to call you, your work, hobbies, daily routine, etc.
    Share as much or as little as you want, or say 'skip'."
   ```
9. **User responds**
10. **Generate user.md**
11. **Show to user for confirmation**
12. **Save configuration and exit onboarding**

**Key Features**:
- Fully conversational, no multiple-choice questions
- User can answer briefly or in detail
- Support agent-assisted generation
- User confirmation at each step

### 4. Heartbeat Mechanism

**Purpose**: Periodic proactive agent checks (not a tool)

**Behavior**:
- Daemon triggers every N minutes (default: 60, configurable)
- Sends message to main agent: `[HEARTBEAT] Please check ~/.vtuber/heartbeat.md tasks. Decide if action is needed. Output <HEARTBEAT_OK> if nothing to do.`
- Main agent reads heartbeat.md in its context
- Agent decides to:
  - Dispatch subagent for tasks
  - Send message to user
  - Output `<HEARTBEAT_OK>` (no action needed)

**Differences from Schedule**:

| Aspect | Heartbeat | Schedule |
|--------|-----------|----------|
| Timing | Drift-tolerant (~hourly) | Exact time |
| Context | Main agent context | Independent subagent |
| Task type | Multi-item review, judgment | Specific task execution |
| Configuration | heartbeat.md file | Dynamic via tools |

### 5. Schedule Tools (APScheduler)

**Tools**:
- `schedule_create`: Create scheduled/recurring task
- `schedule_list`: List all scheduled tasks
- `schedule_cancel`: Cancel a scheduled task

**Tool Annotations**:
```python
from mcp.types import ToolAnnotations

@tool("schedule_create", "Create a scheduled task for the agent to execute", schema,
      annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))

@tool("schedule_list", "List all scheduled tasks", schema,
      annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))

@tool("schedule_cancel", "Cancel a scheduled task", schema,
      annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
```

**Parameters**:
```json
// One-time task
{
  "task_id": "report",
  "task": "Send daily report",
  "trigger_type": "date",
  "trigger_config": {"run_date": "2026-03-05 18:00:00"}
}

// Recurring task - hourly
{
  "task_id": "hourly_check",
  "task": "Check system status",
  "trigger_type": "interval",
  "trigger_config": {"hours": 1}
}

// Cron task - daily at 9am
{
  "task_id": "morning_brief",
  "task": "Send morning brief",
  "trigger_type": "cron",
  "trigger_config": {"hour": 9, "minute": 0}
}
```

**Execution Flow**:
1. APScheduler triggers at scheduled time
2. Daemon creates temporary subagent (independent context)
3. Subagent executes `task` description
4. Results broadcast to all connected clients in real-time

### 6. Memory Tools (with ToolAnnotations)

```python
@tool("memorize", "Store a key-value pair in persistent memory", schema,
      annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))

@tool("recall", "Recall a value from memory", schema,
      annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))

@tool("forget", "Remove a key from memory", schema,
      annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
```

### 7. Command Line Interface

**Main Command** (`vtuber`):
```bash
vtuber start              # Start daemon in background
vtuber stop               # Stop daemon
vtuber status             # Check daemon status
vtuber chat               # Start CLI client and connect to daemon
vtuber restart            # Restart daemon
```

**Standalone Commands**:
```bash
vtuber-daemon             # Start daemon in foreground (debug mode)
vtuber-cli                # Start CLI client directly
```

**Daemon Management**:
- PID file: `~/.vtuber/daemon.pid`
- `start`: Fork to background, write PID
- `stop`: Read PID, send SIGTERM
- `status`: Check if PID process is alive

## Dependencies

```toml
[project]
dependencies = [
    "claude-agent-sdk>=0.1.45",
    "apscheduler>=3.10.0",  # Task scheduling with SQLite persistence
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
]
```

## Implementation Notes

1. **Remove heartbeat tool** - It's now a daemon-level mechanism, not a tool
2. **Split main.py** - Separate daemon entry point and client entry point
3. **Persona loading** - Parse markdown files to generate system_prompt
4. **Default templates** - Create minimal persona.md/user.md if user skips onboarding
5. **Error handling** - Client should gracefully handle daemon not running
6. **Reconnection** - Client should auto-reconnect if daemon restarts

## Migration Path

1. Keep existing `Persona` class, add markdown loading
2. Move current CLI logic to `client/cli.py`
3. Create daemon infrastructure in `daemon/`
4. Update schedule tool to use APScheduler
5. Add ToolAnnotations to all tools
6. Implement onboarding agent
7. Update documentation and examples

## Success Criteria

- ✅ Daemon persists across client sessions
- ✅ Scheduled tasks execute at exact times via subagents
- ✅ Heartbeat triggers periodic main agent checks
- ✅ Multiple clients can connect simultaneously
- ✅ Configuration loaded from markdown files
- ✅ Onboarding agent guides new users interactively
- ✅ All tools have appropriate annotations
