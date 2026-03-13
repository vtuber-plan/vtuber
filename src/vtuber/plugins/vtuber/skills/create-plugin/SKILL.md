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

## How Skills Work

Skills are agent-driven — Claude automatically detects when a skill should activate based on context and invokes it via the `Skill` tool. Each skill has:

- A `SKILL.md` file inside a `skills/<skill-name>/` directory
- Frontmatter with `name` and `description` fields that tell Claude when to activate the skill
- Markdown body with instructions for Claude to follow when the skill is activated

```yaml
---
name: <skill-name>
description: <when Claude should use this skill — describe the trigger context>
---

<instructions for Claude when this skill is activated>
```

## Steps

1. Determine the plugin name and purpose from context
2. Create `.claude-plugin/plugin.json` with name, description, version
3. Create only the components the user actually needs (skills, agents, hooks, MCP servers)
4. Add a `README.md` with usage instructions

## Key Rules

- `name` in plugin.json must be lowercase kebab-case
- Only `plugin.json` goes inside `.claude-plugin/`
- Everything else (skills, agents, hooks) goes at the plugin root
- Skills are directories with `SKILL.md` inside
- All skills are agent-driven (auto-activated by Claude based on context)
