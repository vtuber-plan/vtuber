---
name: create-plugin
description: Scaffold a new Claude Code plugin when the user asks to create, generate, or bootstrap a plugin project. Activates on phrases like "create a plugin", "new plugin", "scaffold plugin", "make a plugin for...".
---

# Plugin Scaffolding Skill

When activated, help the user create a new Claude Code plugin with the correct structure.

## Plugin Directory Structure Reference

```
<plugin-name>/
├── .claude-plugin/
│   └── plugin.json          # Required: plugin manifest
├── commands/                 # User-invocable slash commands (/<plugin>:<cmd>)
│   └── <command>.md
├── skills/                   # Agent Skills (auto-invoked by Claude)
│   └── <skill-name>/
│       └── SKILL.md
├── agents/                   # Subagent definitions
│   └── <agent-name>.md
├── hooks/                    # Event handlers
│   └── hooks.json
├── .mcp.json                 # MCP server configs
├── .lsp.json                 # LSP server configs
├── settings.json             # Default plugin settings
└── README.md                 # Documentation
```

## Steps

1. Determine the plugin name and purpose from context
2. Create `.claude-plugin/plugin.json` with name, description, version
3. Create only the components the user actually needs
4. Add a `README.md` with usage instructions
5. Tell the user how to test: `claude --plugin-dir ./<plugin-name>`

## Key Rules

- `name` in plugin.json must be lowercase kebab-case
- Only `plugin.json` goes inside `.claude-plugin/`
- Everything else (commands, skills, agents, hooks) goes at the plugin root
- Skills are directories with `SKILL.md` inside; commands are plain `.md` files
