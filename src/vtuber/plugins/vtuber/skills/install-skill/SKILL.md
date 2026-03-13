---
name: install-skill
description: Install or add a new skill. Activates when the user asks to "add a skill", "install a skill", "new skill", or wants to integrate a skill into the vtuber.
---

# Install Skill

When activated, help the user install a new skill into the **custom** core plugin.

## Important: All Skills Go Into the VTuber Plugin

Skills MUST be added to the custom plugin at:

```
~/.vtuber/plugins/custom/skills/<skill-name>
```

Do NOT create a separate plugin for each skill. The custom plugin is the central home for all project skills.

## SKILL.md Format

Every skill needs a `SKILL.md` with frontmatter:

```yaml
---
name: <skill-name>
description: <describe when Agent should auto-activate this skill — be specific about trigger phrases and contexts>
---

<Instructions for Agent when this skill is activated.>
```

## Steps

1. **Get Skill** - Retrieve the skill package from the user-provided *filepath*, *URL*, or *other specified location*.
2. **Verify** — Confirm the skill package has a `SKILL.md` with frontmatter.
3. **Install** — Copy the skill package into the `~/.vtuber/plugins/custom/skills/<skill-name>`

## Key Rules

- Each skill is a directory containing `SKILL.md`
- The `description` field in frontmatter is the trigger — make it descriptive and specific
- Skills are agent-driven: Agent auto-activates them based on context matching the description
- Never create a new plugin just for a single skill — integrate into the custom plugin
