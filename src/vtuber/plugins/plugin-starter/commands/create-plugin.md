---
description: Scaffold a new Claude Code plugin project
---

# Create Plugin

Create a new Claude Code plugin project with the correct directory structure.

User request: $ARGUMENTS

## Instructions

Based on the user's description, scaffold a complete plugin project. Follow these steps:

1. **Ask clarifications** (if the user only said a name or was vague):
   - What should the plugin do?
   - Which components are needed? (skills, agents, hooks, MCP servers)

2. **Create the plugin directory** at the path the user specifies (default: `./<plugin-name>/`).

3. **Always create these files:**

### `.claude-plugin/plugin.json` — Plugin manifest

```json
{
  "name": "<plugin-name>",
  "description": "<one-line description>",
  "version": "1.0.0",
  "author": {
    "name": "<user's name or 'Author'>"
  }
}
```

### `README.md` — Documentation

Include: what the plugin does, how to install, how to use each skill/command.

4. **Create requested components:**

### Skills (`skills/<skill-name>/SKILL.md`)

Agent Skills are automatically invoked by Claude based on context. Use frontmatter:

```yaml
---
name: <skill-name>
description: <when Claude should use this skill>
---

<instructions for Claude when this skill is activated>
```

### Commands (`commands/<command-name>.md`)

User-invocable slash commands. Use frontmatter:

```yaml
---
description: <short description shown in /help>
---

<instructions for Claude when user runs this command>
```

### Agents (`agents/<agent-name>.md`)

Subagent definitions. Use frontmatter:

```yaml
---
name: <agent-name>
description: <when to dispatch to this agent>
model: sonnet
tools:
  - Read
  - Grep
  - Glob
---

<system prompt for this agent>
```

### Hooks (`hooks/hooks.json`)

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "<ToolName>",
        "hooks": [{ "type": "command", "command": "<shell command>" }]
      }
    ]
  }
}
```

### MCP servers (`.mcp.json`)

```json
{
  "mcpServers": {
    "<server-name>": {
      "command": "<binary>",
      "args": ["<arg1>"]
    }
  }
}
```

5. **After scaffolding**, print a summary of what was created and how to test:

```
cd <plugin-dir>
claude --plugin-dir .
```

## Important Rules

- Plugin name must be lowercase, kebab-case (e.g., `my-awesome-plugin`)
- Do NOT put skills/commands/agents inside `.claude-plugin/` — only `plugin.json` goes there
- Skills go in `skills/`, commands in `commands/`, agents in `agents/`, hooks in `hooks/`
- Each skill is a **directory** containing `SKILL.md`; each command is a **file** `<name>.md`
