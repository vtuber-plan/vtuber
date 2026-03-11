#!/usr/bin/env python3
"""
Claude Agent SDK 示例 - 使用内置 MCP 添加计算工具并实现对话功能
"""

import asyncio
from typing import Any

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    tool,
    create_sdk_mcp_server,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)


@tool(
    name="add_numbers",
    description="Add two or more numbers together. Pass numbers as an array of integers or floats.",
    input_schema={
    "type": "object",
    "properties": {
        "numbers": {
            "type": "array",
            "items": {
                "type": "number"
            },
            "description": "List of numbers"
        }
    },
    "required": ["numbers"]
}
)
async def add_numbers(args: dict[str, Any]) -> dict[str, Any]:
    """计算数字相加的工具"""
    numbers = args.get("numbers", [])
    if not numbers:
        return {
            "content": [{"type": "text", "text": "Error: No numbers provided"}],
            "is_error": True,
        }

    # 处理可能是字符串的情况（如 "1, 2, 3"）
    if isinstance(numbers, str):
        try:
            # 尝试解析逗号或空格分隔的数字
            numbers = [float(x.strip()) for x in numbers.replace(",", " ").split()]
        except ValueError:
            return {
                "content": [{"type": "text", "text": "Error: Invalid number format. Please provide an array of numbers."}],
                "is_error": True,
            }

    try:
        result = sum(float(n) for n in numbers)
        return {"content": [{"type": "text", "text": f"Sum: {result}"}]}
    except (TypeError, ValueError) as e:
        return {
            "content": [{"type": "text", "text": f"Error: {str(e)}"}],
            "is_error": True,
        }


# 定义另一个计算工具 - 乘法
@tool(
    name="multiply_numbers",
    description="Multiply two or more numbers together. Pass numbers as an array of integers or floats, e.g., {\"numbers\": [2, 3, 4]}",
    input_schema={"numbers": list},
)
async def multiply_numbers(args: dict[str, Any]) -> dict[str, Any]:
    """计算数字相乘的工具"""
    numbers = args.get("numbers", [])
    if not numbers:
        return {
            "content": [{"type": "text", "text": "Error: No numbers provided"}],
            "is_error": True,
        }

    # 处理可能是字符串的情况（如 "2, 3, 4"）
    if isinstance(numbers, str):
        try:
            # 尝试解析逗号或空格分隔的数字
            numbers = [float(x.strip()) for x in numbers.replace(",", " ").split()]
        except ValueError:
            return {
                "content": [{"type": "text", "text": "Error: Invalid number format. Please provide an array of numbers."}],
                "is_error": True,
            }

    try:
        result = 1.0
        for n in numbers:
            result *= float(n)
        return {"content": [{"type": "text", "text": f"Product: {result}"}]}
    except (TypeError, ValueError) as e:
        return {
            "content": [{"type": "text", "text": f"Error: {str(e)}"}],
            "is_error": True,
        }


async def chat_loop(client: ClaudeSDKClient) -> None:
    """实现对话功能"""
    print("=" * 60)
    print("Claude Agent SDK - 对话模式")
    print("=" * 60)
    print("可用工具：add_numbers, multiply_numbers")
    print("输入 'exit' 退出，输入 'clear' 清除上下文")
    print("=" * 60)
    
    turn_count = 0
    
    while True:
        try:
            user_input = input(f"\n[第 {turn_count + 1} 轮] 你：").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() == "exit":
                print("正在结束对话...")
                break
            
            if user_input.lower() == "clear":
                await client.disconnect()
                await client.connect()
                turn_count = 0
                print("上下文已清除，开始新的对话")
                continue
            
            # 发送消息给 Claude
            await client.query(user_input)
            turn_count += 1
            
            # 接收并处理响应
            print(f"[第 {turn_count} 轮] Claude: ", end="", flush=True)
            
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        # 显示工具调用
                        if isinstance(block, ToolUseBlock):
                            print(f"\n🔧 [调用工具：{block.name}] 输入：{block.input}", end="\n", flush=True)
                        # 显示工具执行结果
                        elif isinstance(block, ToolResultBlock):
                            content = block.content
                            if isinstance(content, list) and content:
                                for item in content:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        print(f"\n✅ [工具结果：{item.get('text', '')}]", end="\n", flush=True)
                            elif isinstance(content, str):
                                print(f"\n✅ [工具结果：{content}]", end="\n", flush=True)
                        # 显示文本回复
                        elif isinstance(block, TextBlock):
                            print(f"[Agent]: {block.text}", end="\n", flush=True)
            
            print()  # 换行
            
        except KeyboardInterrupt:
            print("\n\n对话被中断")
            break
        except Exception as e:
            print(f"\n发生错误：{e}")
            break


async def main() -> None:
    """主函数"""
    # 创建内置 MCP 服务器，注册计算工具
    calculator_server = create_sdk_mcp_server(
        name="calculator",
        version="1.0.0",
        tools=[add_numbers, multiply_numbers],
    )
    
    # 配置 Claude Agent 选项
    options = ClaudeAgentOptions(
        # 注册 MCP 服务器
        mcp_servers={"calc": calculator_server},
        # 允许使用自定义工具
        allowed_tools=[
            "mcp__calc__add_numbers",
            "mcp__calc__multiply_numbers",
        ],
        # 设置权限模式为自动接受编辑
        permission_mode="acceptEdits",
        cli_path="ripperdoc",
    )
    
    # 使用 ClaudeSDKClient 进行持续对话
    async with ClaudeSDKClient(options=options) as client:
        await chat_loop(client)
    
    print("对话结束，再见！")


if __name__ == "__main__":
    asyncio.run(main())
