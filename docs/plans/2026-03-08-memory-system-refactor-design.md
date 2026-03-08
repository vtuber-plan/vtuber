# Memory System Refactor Design

**Date**: 2026-03-08
**Status**: Approved
**Reference**: Based on nanobot's proven design patterns

## Overview

Refactor vtuber's memory system to match nanobot's proven architecture: session-per-channel management, dual-layer memory (MEMORY.md + HISTORY.md), and tool-based consolidation.

## Current Problems

1. **Session granularity too fine**: Session IDs like `2026-03-06_12-00-00` (second-precision) create fragmentation
2. **Memory system complexity**: Four-layer memory system is confusing and over-engineered
3. **Consolidation uses free-text parsing**: Unreliable compared to tool-based approach
4. **Tool redundancy**: `search_history` duplicates `grep` functionality

## Design Goals

- Match nanobot's session-per-channel model
- Simplify to dual-layer memory (MEMORY.md + HISTORY.md)
- Use tool-based consolidation for reliability
- Remove redundant tools

## Architecture

### 1. Session Management (nanobot pattern)

**Session Key Format**: `channel:chat_id`

Examples:
- CLI private chat: `cli:main`
- Discord private: `discord:user_123456`
- Discord group: `discord:channel_789012`

**Characteristics**:
- Long-lived sessions (not time-based splitting)
- Each channel/conversation gets its own persistent session
- Files: `~/.vtuber/sessions/<key>.jsonl`

**JSONL Format**:
```jsonl
{"_type": "metadata", "key": "cli:main", "created_at": "2026-03-08T...", "last_consolidated": 50}
{"role": "user", "content": "...", "timestamp": "2026-03-08T10:00:00"}
{"role": "assistant", "content": "...", "timestamp": "2026-03-08T10:00:05"}
```

### 2. Dual-Layer Memory System

**`~/.vtuber/memory/MEMORY.md`** — Long-term Memory
- Always loaded into system prompt
- Stores: preferences, context, relationships, important facts
- Agent can directly Read/Write/Edit this file
- Kept concise (~200 lines)

**`~/.vtuber/memory/HISTORY.md`** — Event Log
- Append-only log
- NOT loaded into context
- Entries start with `[YYYY-MM-DD HH:MM]`
- Search with `grep -i "keyword" ~/.vtuber/memory/HISTORY.md`

**Migration**:
- Move `long_term_memory.md` → `memory/MEMORY.md`
- Move `history.md` → `memory/HISTORY.md`
- Update paths in `config.py`

### 3. Consolidation Mechanism

**Tool-based approach** (nanobot pattern):

```python
_SAVE_MEMORY_TOOL = [{
    "type": "function",
    "function": {
        "name": "save_memory",
        "description": "Save the memory consolidation result.",
        "parameters": {
            "type": "object",
            "properties": {
                "history_entry": {
                    "type": "string",
                    "description": "2-5 sentences summary. Start with [YYYY-MM-DD HH:MM]."
                },
                "memory_update": {
                    "type": "string",
                    "description": "Full updated MEMORY.md. Return unchanged if nothing new."
                }
            },
            "required": ["history_entry", "memory_update"]
        }
    }
}]
```

**Process**:
1. Triggered when session has >50 messages since last consolidation
2. Send old messages to LLM with `save_memory` tool
3. LLM returns `history_entry` (append to HISTORY.md) and `memory_update` (write to MEMORY.md)
4. Update session's `last_consolidated` counter in metadata
5. Messages remain append-only (never modified)

### 4. Tool Simplification

**Keep**:
- `search_sessions(query, limit)` — Search past conversations
- `list_sessions(limit)` — List all sessions
- `read_session(session_id)` — Read specific session

**Remove**:
- `search_history` — Users can use `Bash + grep` directly

**Result**: Cleaner tool surface, leverages built-in Bash tool.

### 5. Simplified System Prompt

```markdown
## Memory System

- `memory/MEMORY.md` — Long-term facts (preferences, context). Always in your context.
- `memory/HISTORY.md` — Append-only event log. NOT loaded. Search with grep.

## Search Past Events

Use Bash tool:
```bash
grep -i "keyword" ~/.vtuber/memory/HISTORY.md
```

## When to Update MEMORY.md

Write important facts immediately using Read/Write/Edit:
- User preferences
- Project context
- Important relationships

## Auto-consolidation

Old conversations are automatically summarized to HISTORY.md and MEMORY.md. You don't manage this.
```

## Directory Structure

```
~/.vtuber/
├── memory/
│   ├── MEMORY.md          # Long-term memory (in context)
│   └── HISTORY.md         # Event log (grep search)
├── sessions/
│   ├── cli_main.jsonl
│   ├── discord_user_123.jsonl
│   └── discord_channel_456.jsonl
├── persona.md
├── user.md
├── HEARTBEAT.md
└── config.yaml
```

## Implementation Tasks

1. Update `config.py` with new memory paths
2. Refactor `tools/memory.py`:
   - Add `Session` dataclass with metadata
   - Add `SessionManager` class
   - Remove `search_history` tool
   - Update session ID generation
3. Refactor `daemon/heartbeat.py` consolidation:
   - Add `_SAVE_MEMORY_TOOL` definition
   - Implement tool-based consolidation
   - Track `last_consolidated` in session metadata
4. Update `persona.py` system prompt
5. Create migration script for existing files
6. Update `daemon/server.py` to use session keys
7. Update all session ID references throughout codebase

## Benefits

1. **Simpler mental model**: Two files instead of four layers
2. **Proven patterns**: Directly copied from nanobot's working system
3. **Better tooling**: Tool-based consolidation > free-text parsing
4. **Cleaner sessions**: Logical grouping by channel instead of arbitrary time slices
5. **Less code**: Remove redundant tools and complexity

## Trade-offs

- **Breaking change**: Old session files incompatible (need migration)
- **Less guidance**: Simpler prompt means agent needs to learn grep usage
- **Path changes**: Existing long_term_memory.md needs migration

## Success Criteria

- Session files use `channel:chat_id` naming
- MEMORY.md and HISTORY.md exist in `~/.vtuber/memory/`
- Consolidation uses `save_memory` tool
- No `search_history` tool exists
- System prompt matches nanobot's simplicity
- Existing data migrated successfully
