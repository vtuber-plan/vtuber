"""Main entry point - wires together persona, tools, interface, and SDK client."""

import asyncio
import sys

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    TextBlock,
    ResultMessage,
    create_sdk_mcp_server,
)

from vtuber.persona import Persona
from vtuber.tools.schedule import schedule
from vtuber.tools.heartbeat import heartbeat
from vtuber.tools.memory import memorize, recall, forget
from vtuber.interface.cli import CLIInterface


def build_options(persona: Persona) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions from a persona and built-in tools."""
    all_tools = [*schedule, heartbeat, memorize, recall, forget]

    server = create_sdk_mcp_server(
        name="vtuber-tools",
        version="0.1.0",
        tools=all_tools,
    )

    tool_names = [f"mcp__vtuber-tools__{t.name}" for t in all_tools]

    return ClaudeAgentOptions(
        system_prompt=persona.to_system_prompt(),
        mcp_servers={"vtuber-tools": server},
        allowed_tools=tool_names,
        # Safe: all tools are in-process Python functions with no system access
        permission_mode="bypassPermissions",
    )


async def run(persona: Persona | None = None) -> None:
    """Run the vtuber agent with the given persona and CLI interface."""
    if persona is None:
        persona = Persona()

    interface = CLIInterface(prompt=f"[{persona.name}] You> ")
    options = build_options(persona)

    print(f"=== {persona.name} 已上线 ===")
    print(f"{persona.description}")
    print("输入 /quit 退出\n")

    async with ClaudeSDKClient(options=options) as client:
        async for user_input in interface.run():
            await interface.send_typing()
            await client.query(user_input)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            await interface.send_message(
                                f"[{persona.name}] {block.text}"
                            )
                elif isinstance(message, ResultMessage):
                    if message.is_error:
                        await interface.send_message(
                            f"[系统] 发生错误: {message.result}"
                        )

    print(f"\n=== {persona.name} 已下线 ===")


def cli() -> None:
    """CLI entry point."""
    asyncio.run(run())


if __name__ == "__main__":
    cli()
