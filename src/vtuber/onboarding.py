"""Onboarding flow for first-time users."""

from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    ToolPermissionContext,
)

import shutil

from vtuber.config import ensure_config_dir, get_config_path, get_persona_path, get_user_path, get_heartbeat_path, ensure_workspace_dir
from vtuber.templates import DEFAULT_PERSONA, DEFAULT_USER, DEFAULT_HEARTBEAT, DEFAULT_CONFIG
from vtuber.utils import extract_stream_text

console = Console()
prompt_session = PromptSession()


def _get_allowed_files() -> set[str]:
    """Compute allowed file paths at call time (not import time)."""
    return {
        str(get_persona_path().resolve()),
        str(get_user_path().resolve()),
    }


async def _onboarding_permission(
    tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    """Restrict AI to only read/write persona.md and user.md."""
    allowed = _get_allowed_files()
    if tool_name in ("Write", "Edit", "MultiEdit"):
        file_path = str(Path(tool_input.get("file_path", "")).resolve())
        if file_path not in allowed:
            return PermissionResultDeny(message="Onboarding 只允许写入 persona.md 和 user.md")
        return PermissionResultAllow()
    if tool_name == "Read":
        file_path = str(Path(tool_input.get("file_path", "")).resolve())
        if file_path not in allowed:
            return PermissionResultDeny(message="Onboarding 只允许读取 persona.md 和 user.md")
        return PermissionResultAllow()
    return PermissionResultDeny(message=f"Onboarding 不允许使用 {tool_name} 工具")


ONBOARDING_SYSTEM_PROMPT = """你是 VTuber 数字生命助手的初次设置引导员。你的任务是帮助用户完成两个配置文件的设置。

## 你的工作方式

用户会给你一段文字描述，你需要：
1. 根据描述总结成结构化的 markdown 内容
2. 将总结呈现给用户看
3. 等待用户确认或提出修改意见
4. 用户确认后，使用 Write 工具将内容写入对应文件

## 文件说明

### {user_path} — 用户档案
存储用户的个人信息，帮助 AI 更好地了解和服务用户。参考格式：

```markdown
{default_user}
```

你可以根据用户提供的信息自由调整格式和内容，不必严格遵循模板。

### {persona_path} — AI 人格设定
存储 AI 助手的人格特征，决定 AI 的行为方式。参考格式：

```markdown
{default_persona}
```

同样可以根据用户描述自由调整。

## 重要规则

- 使用中文交流
- 总结后必须等用户确认才能写入文件
- 写入文件路径必须是 {user_path} 或 {persona_path}
- 保持简洁友好
"""


async def _query_and_collect(
    agent: ClaudeSDKClient, prompt: str, print_stream: bool = True
) -> str:
    """Send a query to the agent and collect the full text response."""
    await agent.query(prompt)
    collected = ""
    async for msg in agent.receive_response():
        text = extract_stream_text(msg)
        if text:
            collected += text
        elif isinstance(msg, ResultMessage):
            break
    if print_stream and collected.strip():
        console.print()
        console.print(
            Panel(
                Markdown(collected.strip()),
                title="[bold cyan]AI[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
    return collected


async def _run_phase(
    agent: ClaudeSDKClient,
    phase_name: str,
    prompt_text: str,
    ai_instruction: str,
    target_path: Path,
):
    """Run one onboarding phase: user input -> AI summarize -> confirm/revise loop -> write file."""
    console.print()
    console.print(Rule(f"[bold]{phase_name}[/bold]", style="blue"))
    console.print()
    console.print(f"[bold]{prompt_text}[/bold]")

    user_input = await prompt_session.prompt_async(
        HTML("<ansigreen><b>You</b></ansigreen> <ansigray>›</ansigray> ")
    )

    await _query_and_collect(agent, ai_instruction + user_input)

    while not target_path.exists():
        try:
            user_reply = await prompt_session.prompt_async(
                HTML("<ansigreen><b>You</b></ansigreen> <ansigray>›</ansigray> ")
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]跳过此阶段，使用默认配置。[/yellow]")
            return
        await _query_and_collect(agent, user_reply)

    console.print(f"\n[green bold]✓[/green bold] {phase_name} 已保存到 [dim]{target_path}[/dim]")


async def run_onboarding():
    """Run the interactive onboarding flow."""
    ensure_config_dir()

    user_path = str(get_user_path())
    persona_path = str(get_persona_path())

    system_prompt = ONBOARDING_SYSTEM_PROMPT.format(
        user_path=user_path,
        persona_path=persona_path,
        default_user=DEFAULT_USER.strip(),
        default_persona=DEFAULT_PERSONA.strip(),
    )

    console.print()
    console.print(
        Panel(
            "[bold]欢迎使用 VTuber 数字生命助手！[/bold]\n"
            "[dim]Welcome to VTuber Digital Life Assistant![/dim]\n\n"
            "这是您第一次运行，让我们完成初始设置。",
            title="[bold magenta]VTuber Setup[/bold magenta]",
            border_style="magenta",
            padding=(1, 2),
        )
    )

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        tools=["Read", "Write", "Edit", "MultiEdit"],
        can_use_tool=_onboarding_permission,
    )
    agent = ClaudeSDKClient(options)
    await agent.connect()

    try:
        await _run_phase(
            agent=agent,
            phase_name="用户档案",
            prompt_text="请简单介绍一下自己（称呼、职业、兴趣、偏好等，随意发挥）：",
            ai_instruction=(
                "用户将介绍自己。请根据用户的描述，总结成 user.md 的内容并呈现给用户。"
                "呈现后告诉用户：如果满意请回复「确认」，如果想修改请直接说明。"
                f"确认后用 Write 工具写入 {user_path}。\n\n用户说："
            ),
            target_path=get_user_path(),
        )

        await _run_phase(
            agent=agent,
            phase_name="AI 人格设定",
            prompt_text="请描述你希望 AI 助手是什么样的（名字、性格、说话风格、背景故事等，随意发挥）：",
            ai_instruction=(
                "用户将描述他们希望的 AI 助手设定。请根据描述，总结成 persona.md 的内容并呈现给用户。"
                "呈现后告诉用户：如果满意请回复「确认」，如果想修改请直接说明。"
                f"确认后用 Write 工具写入 {persona_path}。\n\n用户说："
            ),
            target_path=get_persona_path(),
        )
    finally:
        await agent.disconnect()

    console.print()
    console.print(
        Panel(
            "[bold green]设置完成！[/bold green] 您的 VTuber 数字生命助手已准备就绪。\n\n"
            "运行 [bold]vtuber start[/bold] 启动 daemon\n"
            "运行 [bold]vtuber chat[/bold] 开始对话",
            title="[bold magenta]Setup Complete[/bold magenta]",
            border_style="green",
            padding=(1, 2),
        )
    )


async def check_and_run_onboarding():
    """Check if onboarding is needed and run it."""
    persona_path = get_persona_path()
    user_path = get_user_path()

    if persona_path.exists() and user_path.exists():
        return False

    await run_onboarding()
    return True


def create_default_configs():
    """Create default configuration files without interactive prompts."""
    ensure_config_dir()

    persona_path = get_persona_path()
    user_path = get_user_path()
    heartbeat_path = get_heartbeat_path()
    config_path = get_config_path()

    if not persona_path.exists():
        persona_path.write_text(DEFAULT_PERSONA, encoding="utf-8")

    if not user_path.exists():
        user_path.write_text(DEFAULT_USER, encoding="utf-8")

    if not heartbeat_path.exists():
        heartbeat_path.write_text(DEFAULT_HEARTBEAT, encoding="utf-8")

    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")

    # Copy built-in commands to workspace .claude/commands/ (Claude's native loading)
    builtin_skills = Path(__file__).parent / "skills"
    commands_dir = ensure_workspace_dir() / ".claude" / "commands"
    if builtin_skills.is_dir():
        commands_dir.mkdir(parents=True, exist_ok=True)
        for skill_dir in builtin_skills.iterdir():
            if skill_dir.is_dir() and not skill_dir.name.startswith("_"):
                skill_md = skill_dir / "SKILL.md"
                target = commands_dir / f"{skill_dir.name}.md"
                if not target.exists() and skill_md.exists():
                    shutil.copy2(skill_md, target)
