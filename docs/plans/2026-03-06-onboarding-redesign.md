# Onboarding Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 重写 onboarding 流程，让 AI 通过 SDK 的文件工具直接写入 `~/.vtuber/persona.md` 和 `user.md`，Python 端用 `can_use_tool` 做路径校验。

**Architecture:** 两阶段交互流程（用户介绍 → AI 设定），每阶段：用户输入 → AI 总结呈现 → 用户确认/修改循环 → AI 写文件。用 `can_use_tool` 回调限制只能读写这两个文件，不使用 `bypassPermissions`。

**Tech Stack:** Python, claude-agent-sdk (`ClaudeSDKClient`, `can_use_tool`, `PermissionResultAllow/Deny`)

---

### Task 1: 重写 onboarding.py — 权限回调与 agent 创建

**Files:**
- Modify: `src/vtuber/onboarding.py`

**Step 1: 重写 import 和权限回调函数**

替换 `onboarding.py` 的全部内容。先写权限回调和辅助函数：

```python
"""Onboarding flow for first-time users."""

import asyncio
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolPermissionContext,
)

from vtuber.config import ensure_config_dir, get_persona_path, get_user_path
from vtuber.templates import DEFAULT_PERSONA, DEFAULT_USER

# Onboarding 允许 AI 读写的文件白名单
ALLOWED_FILES = {
    str(get_persona_path()),
    str(get_user_path()),
}


async def _onboarding_permission(
    tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    """Restrict AI to only read/write persona.md and user.md."""
    if tool_name in ("Write", "Edit", "MultiEdit"):
        file_path = tool_input.get("file_path", "")
        if file_path not in ALLOWED_FILES:
            return PermissionResultDeny(message="Onboarding 只允许写入 persona.md 和 user.md")
        return PermissionResultAllow()
    if tool_name == "Read":
        file_path = tool_input.get("file_path", "")
        if file_path not in ALLOWED_FILES:
            return PermissionResultDeny(message="Onboarding 只允许读取 persona.md 和 user.md")
        return PermissionResultAllow()
    # Allow other tools (e.g. thinking) — but disallowed_tools will block most
    return PermissionResultAllow()


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
```

**Step 2: 验证 import 可用**

Run: `cd /Users/lex/Codes/vtuber && python -c "from vtuber.onboarding import _onboarding_permission; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/vtuber/onboarding.py
git commit -m "refactor(onboarding): add can_use_tool permission callback"
```

---

### Task 2: 重写 onboarding.py — system prompt 与 run_onboarding

**Files:**
- Modify: `src/vtuber/onboarding.py`

**Step 1: 添加 system prompt 常量和 run_onboarding**

在 `_extract_stream_text` 之后追加：

```python
ONBOARDING_SYSTEM_PROMPT = f"""你是 VTuber 数字生命助手的初次设置引导员。你的任务是帮助用户完成两个配置文件的设置。

## 你的工作方式

用户会给你一段文字描述，你需要：
1. 根据描述总结成结构化的 markdown 内容
2. 将总结呈现给用户看
3. 等待用户确认或提出修改意见
4. 用户确认后，使用 Write 工具将内容写入对应文件

## 文件说明

### ~/.vtuber/user.md — 用户档案
存储用户的个人信息，帮助 AI 更好地了解和服务用户。参考格式：

```markdown
{DEFAULT_USER.strip()}
```

你可以根据用户提供的信息自由调整格式和内容，不必严格遵循模板。

### ~/.vtuber/persona.md — AI 人格设定
存储 AI 助手的人格特征，决定 AI 的行为方式。参考格式：

```markdown
{DEFAULT_PERSONA.strip()}
```

同样可以根据用户描述自由调整。

## 重要规则

- 使用中文交流
- 总结后必须等用户确认才能写入文件
- 写入文件路径必须是 {str(get_user_path())} 或 {str(get_persona_path())}
- 保持简洁友好
"""


async def _query_and_collect(
    agent: ClaudeSDKClient, prompt: str, print_stream: bool = True
) -> str:
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
    if print_stream:
        print()  # Newline after stream
    return collected


async def run_onboarding():
    """Run the interactive onboarding flow."""
    ensure_config_dir()

    print("\n" + "=" * 60)
    print("  欢迎使用 VTuber 数字生命助手！")
    print("  Welcome to VTuber Digital Life Assistant!")
    print("=" * 60)
    print("\n这是您第一次运行，让我们完成初始设置。\n")

    # Create onboarding agent with restricted permissions
    options = ClaudeAgentOptions(
        system_prompt=ONBOARDING_SYSTEM_PROMPT,
        tools=["Read", "Write", "Edit", "MultiEdit"],
        can_use_tool=_onboarding_permission,
    )
    agent = ClaudeSDKClient(options)
    await agent.connect()

    try:
        # Phase 1: User profile
        await _run_phase(
            agent=agent,
            phase_name="用户档案",
            prompt_text="请简单介绍一下自己（称呼、职业、兴趣、偏好等，随意发挥）：",
            ai_instruction=(
                "用户将介绍自己。请根据用户的描述，总结成 user.md 的内容并呈现给用户。"
                "呈现后告诉用户：如果满意请回复「确认」，如果想修改请直接说明。"
                f"确认后用 Write 工具写入 {str(get_user_path())}。\n\n用户说："
            ),
            target_path=get_user_path(),
        )

        # Phase 2: AI persona
        await _run_phase(
            agent=agent,
            phase_name="AI 人格设定",
            prompt_text="请描述你希望 AI 助手是什么样的（名字、性格、说话风格、背景故事等，随意发挥）：",
            ai_instruction=(
                "用户将描述他们希望的 AI 助手设定。请根据描述，总结成 persona.md 的内容并呈现给用户。"
                "呈现后告诉用户：如果满意请回复「确认」，如果想修改请直接说明。"
                f"确认后用 Write 工具写入 {str(get_persona_path())}。\n\n用户说："
            ),
            target_path=get_persona_path(),
        )
    finally:
        await agent.disconnect()

    print("\n" + "=" * 60)
    print("  设置完成！您的 VTuber 数字生命助手已准备就绪。")
    print("=" * 60)
    print("\n运行 'vtuber chat' 开始对话\n")
```

**Step 2: 验证语法**

Run: `cd /Users/lex/Codes/vtuber && python -c "from vtuber.onboarding import run_onboarding; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/vtuber/onboarding.py
git commit -m "refactor(onboarding): add system prompt and run_onboarding skeleton"
```

---

### Task 3: 重写 onboarding.py — 阶段循环与入口函数

**Files:**
- Modify: `src/vtuber/onboarding.py`

**Step 1: 添加 _run_phase 和保留的入口函数**

在 `run_onboarding` 之后追加：

```python
async def _run_phase(
    agent: ClaudeSDKClient,
    phase_name: str,
    prompt_text: str,
    ai_instruction: str,
    target_path: Path,
):
    """Run one onboarding phase: user input → AI summarize → confirm/revise loop → write file."""
    print(f"\n--- {phase_name} ---\n")

    # Get user input
    user_input = input(prompt_text + "\n> ")

    # Send to AI with instruction
    await _query_and_collect(agent, ai_instruction + user_input)

    # Confirm/revise loop — exit when file is written
    while not target_path.exists():
        user_reply = input("\n> ")
        await _query_and_collect(agent, user_reply)

    print(f"\n✓ {phase_name} 已保存到 {target_path}")


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

    if not persona_path.exists():
        persona_path.write_text(DEFAULT_PERSONA, encoding="utf-8")

    if not user_path.exists():
        user_path.write_text(DEFAULT_USER, encoding="utf-8")
```

**Step 2: 验证完整模块**

Run: `cd /Users/lex/Codes/vtuber && python -c "from vtuber.onboarding import check_and_run_onboarding, create_default_configs, _run_phase; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/vtuber/onboarding.py
git commit -m "refactor(onboarding): add phase loop and entry functions, remove old parsing logic"
```

---

### Task 4: 手动集成测试

**Files:**
- 无文件修改，仅测试

**Step 1: 确认 daemon server.py 的 onboarding 调用兼容**

检查 `src/vtuber/daemon/server.py` 中对 `onboarding` 的引用：
- `from vtuber.onboarding import create_default_configs` — 仍存在，OK
- `from vtuber.onboarding import check_and_run_onboarding` — 仍存在，OK

Run: `cd /Users/lex/Codes/vtuber && python -c "from vtuber.daemon.server import start_daemon_background; print('OK')"`
Expected: `OK`

**Step 2: 清理 ~/.vtuber 测试文件并做 dry-run**

先备份现有配置（如有），然后删除 user.md 和 persona.md，运行 onboarding：

```bash
# 备份
mkdir -p /tmp/vtuber-backup
cp ~/.vtuber/user.md /tmp/vtuber-backup/ 2>/dev/null || true
cp ~/.vtuber/persona.md /tmp/vtuber-backup/ 2>/dev/null || true

# 删除配置以触发 onboarding
rm -f ~/.vtuber/user.md ~/.vtuber/persona.md

# 运行 onboarding（交互式，需要手动输入）
cd /Users/lex/Codes/vtuber && python -c "
import asyncio
from vtuber.onboarding import check_and_run_onboarding
asyncio.run(check_and_run_onboarding())
"
```

Expected: 两阶段交互流程正常运行，AI 总结并写入文件。

**Step 3: 验证文件已写入**

```bash
cat ~/.vtuber/user.md
cat ~/.vtuber/persona.md
```

Expected: 两个文件存在且内容为 AI 生成的 markdown。

**Step 4: 验证重复运行不触发 onboarding**

```bash
cd /Users/lex/Codes/vtuber && python -c "
import asyncio
from vtuber.onboarding import check_and_run_onboarding
result = asyncio.run(check_and_run_onboarding())
print(f'Onboarding needed: {result}')
"
```

Expected: `Onboarding needed: False`

**Step 5: 恢复备份（可选）**

```bash
cp /tmp/vtuber-backup/user.md ~/.vtuber/ 2>/dev/null || true
cp /tmp/vtuber-backup/persona.md ~/.vtuber/ 2>/dev/null || true
```

**Step 6: Final commit**

```bash
git add -A
git commit -m "refactor(onboarding): complete rewrite — AI writes config files directly"
```
