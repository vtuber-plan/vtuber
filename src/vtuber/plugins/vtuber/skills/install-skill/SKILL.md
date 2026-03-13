---
name: install-skill
description: Install, create, or add a new skill. Activates when the user asks to "add a skill", "create a skill", "install a skill", "new skill", or wants to integrate a skill into the vtuber.
---

# Install / Create Skill

When activated, help the user create or install a new skill into the **vtuber** core plugin.

## Important: All Skills Go Into the VTuber Plugin

Skills MUST be added to the vtuber plugin at:

```
~/.vtuber/plugins/vtuber/skills/<skill-name>/SKILL.md
```

Do NOT create a separate plugin for each skill. The vtuber plugin is the central home for all project skills.

## SKILL.md Format

Every skill needs a `SKILL.md` with frontmatter:

```yaml
---
name: <skill-name>
description: <describe when Claude should auto-activate this skill — be specific about trigger phrases and contexts>
---

# <Skill Title>

<Instructions for Claude when this skill is activated.>
<Include steps, rules, examples, and templates as needed.>
```

## Steps

1. **Clarify** — Ask what the skill should do if the user is vague
2. **Name** — Choose a lowercase kebab-case name (e.g., `code-review`, `deploy-check`)
3. **Write the description** — This is critical: it determines when Claude auto-activates the skill. Be specific about trigger phrases and contexts
4. **Write the body** — Clear, actionable instructions for Claude to follow
5. **Create the file** at `~/.vtuber/plugins/vtuber/skills/<skill-name>/SKILL.md`
6. **Verify** — Confirm the skill shows up in the available skills list

## Key Rules

- Skill name must be lowercase kebab-case
- Each skill is a directory containing `SKILL.md`
- The `description` field in frontmatter is the trigger — make it descriptive and specific
- Skills are agent-driven: Claude auto-activates them based on context matching the description
- Never create a new plugin just for a single skill — integrate into the vtuber plugin
