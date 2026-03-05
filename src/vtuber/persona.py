"""Persona system - build system prompt from persona.md, user.md, and long-term memory."""

from pathlib import Path

from vtuber.config import get_long_term_memory_path, get_user_path
from vtuber.templates import DEFAULT_PERSONA, DEFAULT_USER

TOOLS_SECTION = """## 内置能力

你拥有 Claude 的全部内置工具（Read、Write、Edit、Bash、Grep、Glob 等），以及以下自定义工具：

### 记忆系统
- **search_sessions(query, limit)**: 搜索过往对话记录，按关键词查找历史消息
- **list_sessions(limit)**: 列出最近的对话 session 及其摘要
- **update_long_term_memory(content)**: 向长期记忆追加重要洞察（请克制使用，仅记录跨 session 有价值的模式和事实）

### 日程管理
- **schedule_create(task_id, task, trigger_type, trigger_config)**: 创建定时任务
- **schedule_list()**: 列出所有定时任务
- **schedule_cancel(task_id)**: 取消定时任务

## 关于记忆

### 短期记忆
每次对话都会自动记录到 session log 中。你可以用 search_sessions 搜索历史对话。

### 长期记忆
你的长期记忆文件会被注入到这个系统提示中（见下方）。请只在有真正重要的、跨 session 的洞察时才更新长期记忆。

### 用户档案
你可以通过 Write 工具写入 {user_path} 来更新你对用户的了解。当你在对话中获得了关于用户的新信息（偏好、习惯、重要事件等），可以主动更新这个文件。

请自然地使用这些能力来增强交互体验。"""

LONG_TERM_MEMORY_HEADER = """## 长期记忆

以下是你主动记录的长期记忆内容：

"""


def _read_or_default(path: Path, default: str) -> str:
    """Read file content, falling back to default if missing or empty."""
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
        if content:
            return content
    return default.strip()


def _read_long_term_memory() -> str:
    """Read long-term memory file, return empty string if not exists."""
    memory_path = get_long_term_memory_path()
    if memory_path.exists():
        content = memory_path.read_text(encoding="utf-8").strip()
        if content:
            return LONG_TERM_MEMORY_HEADER + content
    return ""


def build_system_prompt(persona_path: Path, user_path: Path) -> str:
    """Build system prompt from persona.md, user.md, long-term memory, and tools section."""
    persona_content = _read_or_default(persona_path, DEFAULT_PERSONA)
    user_content = _read_or_default(user_path, DEFAULT_USER)
    tools_section = TOOLS_SECTION.format(user_path=str(get_user_path()))
    long_term_memory = _read_long_term_memory()

    parts = [
        persona_content,
        "---",
        user_content,
        "---",
        tools_section,
    ]

    if long_term_memory:
        parts.extend(["---", long_term_memory])

    return "\n\n".join(parts)
