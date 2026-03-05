"""Onboarding flow for first-time users."""

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    TextBlock,
)

from vtuber.config import ensure_config_dir, get_persona_path, get_user_path
from vtuber.templates import DEFAULT_PERSONA, DEFAULT_USER


def _extract_stream_text(msg) -> str | None:
    """Extract text from a StreamEvent or AssistantMessage."""
    if isinstance(msg, StreamEvent):
        event = msg.event
        if event.get("type") == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                return delta.get("text", "")
        return None

    if isinstance(msg, AssistantMessage):
        parts = []
        for block in msg.content:
            if isinstance(block, TextBlock) and block.text:
                parts.append(block.text)
        return "".join(parts) if parts else None

    return None


async def check_and_run_onboarding():
    """Check if onboarding is needed and run it."""
    persona_path = get_persona_path()
    user_path = get_user_path()

    # Check if config files exist
    if persona_path.exists() and user_path.exists():
        return False  # Already onboarded

    # Run onboarding
    await run_onboarding()
    return True


async def run_onboarding():
    """Run the interactive onboarding flow."""
    ensure_config_dir()

    print("\n" + "=" * 60)
    print("欢迎使用 VTuber 数字生命助手！")
    print("Welcome to VTuber Digital Life Assistant!")
    print("=" * 60)
    print("\n这是您第一次运行，让我帮您完成初始设置...")
    print("This is your first run. Let me help you with the initial setup...\n")

    # Create onboarding agent
    options = ClaudeAgentOptions(
        system_prompt="""你是一个友好的入门引导助手，帮助用户设置他们的 VTuber 数字生命助手。

你的任务是：
1. 询问用户希望助手有什么样的人格特点
2. 询问用户的称呼和基本信息
3. 根据用户的回答生成配置文件

请使用中文进行交流，保持友好和简洁。""",
        permission_mode="bypassPermissions",
    )
    onboarding_agent = ClaudeSDKClient(options)
    await onboarding_agent.connect()

    try:
        # Step 1: Ask about persona
        print("第一步：让我们设置助手的人格特点")
        print("Step 1: Let's set up your assistant's personality\n")

        persona_response = await _ask_about_persona(onboarding_agent)
        await _save_persona(persona_response)

        # Step 2: Ask about user
        print("\n第二步：让我们了解一下您")
        print("Step 2: Let's learn about you\n")

        user_response = await _ask_about_user(onboarding_agent)
        await _save_user(user_response)
    finally:
        await onboarding_agent.disconnect()

    print("\n" + "=" * 60)
    print("设置完成！您的 VTuber 数字生命助手已经准备好了。")
    print("Setup complete! Your VTuber Digital Life Assistant is ready.")
    print("=" * 60)
    print("\n您可以随时运行 'vtuber chat' 开始对话")
    print("You can start a conversation anytime with 'vtuber chat'\n")


async def _query_and_collect(agent: ClaudeSDKClient, prompt: str, print_stream: bool = True) -> str:
    """Send a query to the agent and collect the full text response."""
    await agent.query(prompt)
    collected = ""
    async for msg in agent.receive_response():
        text = _extract_stream_text(msg)
        if text:
            collected += text
            if print_stream:
                print(text, end="", flush=True)
        elif isinstance(msg, ResultMessage):
            break
    return collected


async def _ask_about_persona(agent: ClaudeSDKClient) -> str:
    """Ask user about assistant personality preferences."""
    prompt = """请向用户询问以下问题（用中文）：

1. 您希望助手叫什么名字？
2. 您希望助手有什么样的性格特点？（例如：友好、专业、幽默等）
3. 您希望助手的说话风格是怎样的？（例如：正式、随意、温暖等）

请根据用户的回答，生成一份人格配置的总结。格式如下：

Name: [名字]
Description: [简短描述]
Traits: [性格特点列表]
Speaking Style: [说话风格]

请等待用户输入后再生成总结。"""

    print("正在启动引导对话...")
    print("Starting onboarding conversation...\n")

    # Get initial question from agent
    await _query_and_collect(agent, prompt, print_stream=True)
    print()  # Newline

    # Get user input
    user_input = input("\n您的回答: ")

    # Get agent's response
    response_text = await _query_and_collect(agent, user_input, print_stream=True)
    print("\n")
    return response_text


async def _ask_about_user(agent: ClaudeSDKClient) -> str:
    """Ask user about themselves."""
    prompt = """现在请询问用户的基本信息：

1. 您希望助手怎么称呼您？
2. 您的职业或兴趣是什么？（可选）

请根据用户的回答，生成一份用户信息总结。格式如下：

Name: [用户称呼]
Background: [背景信息]

请等待用户输入后再生成总结。"""

    # Get question from agent
    await _query_and_collect(agent, prompt, print_stream=True)
    print()  # Newline

    # Get user input
    user_input = input("\n您的回答: ")

    # Get agent's response
    response_text = await _query_and_collect(agent, user_input, print_stream=True)
    print("\n")
    return response_text


async def _save_persona(persona_text: str):
    """Save persona configuration to file."""
    persona_path = get_persona_path()

    # If agent didn't provide proper format, use default
    if not persona_text.strip() or "Name:" not in persona_text:
        print("使用默认人格配置...")
        persona_path.write_text(DEFAULT_PERSONA, encoding="utf-8")
        return

    # Convert to markdown format
    lines = persona_text.strip().split("\n")
    markdown_lines = ["# Persona Configuration\n"]

    for line in lines:
        if line.startswith("Name:"):
            markdown_lines.append("## Basic Info")
            markdown_lines.append(f"- {line}")
        elif line.startswith("Description:"):
            markdown_lines.append(f"- {line}")
        elif line.startswith("Traits:"):
            markdown_lines.append("\n## Personality Traits")
            traits = line.replace("Traits:", "").strip()
            for trait in traits.split(","):
                markdown_lines.append(f"- {trait.strip()}")
        elif line.startswith("Speaking Style:"):
            markdown_lines.append("\n## Speaking Style")
            style = line.replace("Speaking Style:", "").strip()
            markdown_lines.append(f"- {style}")
        else:
            markdown_lines.append(line)

    markdown_content = "\n".join(markdown_lines)
    persona_path.write_text(markdown_content, encoding="utf-8")
    print(f"人格配置已保存到: {persona_path}")


async def _save_user(user_text: str):
    """Save user information to file."""
    user_path = get_user_path()

    # If agent didn't provide proper format, use default
    if not user_text.strip() or "Name:" not in user_text:
        print("使用默认用户配置...")
        user_path.write_text(DEFAULT_USER, encoding="utf-8")
        return

    # Convert to markdown format
    lines = user_text.strip().split("\n")
    markdown_lines = ["# User Information\n", "## Basic Info"]

    for line in lines:
        if line.startswith("Name:") or line.startswith("Background:"):
            markdown_lines.append(f"- {line}")
        else:
            markdown_lines.append(line)

    markdown_content = "\n".join(markdown_lines)
    user_path.write_text(markdown_content, encoding="utf-8")
    print(f"用户信息已保存到: {user_path}")


def create_default_configs():
    """Create default configuration files without interactive prompts."""
    ensure_config_dir()

    persona_path = get_persona_path()
    user_path = get_user_path()

    if not persona_path.exists():
        persona_path.write_text(DEFAULT_PERSONA, encoding="utf-8")

    if not user_path.exists():
        user_path.write_text(DEFAULT_USER, encoding="utf-8")
