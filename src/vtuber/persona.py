"""Persona system - build system prompt from persona.md, user.md, and long-term memory."""

from pathlib import Path

from vtuber.config import get_long_term_memory_path, get_history_path, get_user_path
from vtuber.templates import DEFAULT_PERSONA, DEFAULT_USER

TOOLS_SECTION = """## 内置能力

你拥有操作计算机的基本能力(Read、Write、Edit、Bash、Grep、Glob 等），以及以下自定义工具：

### 对话记忆
- **search_sessions(query, limit)**: 搜索过往对话记录，返回匹配消息及上下文
- **list_sessions(limit)**: 列出最近的对话 session 及话题预览
- **read_session(session_id)**: 读取某次对话的完整内容
- **search_history(query, limit)**: 搜索事件日志 (history.md)，每条以 [YYYY-MM-DD HH:MM] 开头

### 日程管理
- **schedule_create(task_id, task, trigger_type, trigger_config)**: 创建定时任务
  - trigger_type: "date"（一次性定时）/ "interval"（固定间隔）/ "cron"（cron 表达式）
  - 示例: `trigger_type="cron", trigger_config={{"hour": 9, "minute": 0}}` — 每天9点
  - 示例: `trigger_type="date", trigger_config={{"run_date": "2026-07-22 09:00:00"}}` — 一次性
  - 示例: `trigger_type="cron", trigger_config={{"day_of_week": "mon-fri", "hour": 9, "timezone": "Asia/Shanghai"}}` — 工作日9点（上海时区）
- **schedule_list()**: 列出所有定时任务
- **schedule_cancel(task_id)**: 取消定时任务

## 记忆管理

你有四层记忆，请善用它们：

### 1. 对话记录（自动）
每轮对话自动记录到 session log。用 search_sessions / list_sessions / read_session 查询。

### 2. 长期记忆（你维护）
文件路径：`{long_term_memory_path}`
该文件的内容会注入到你的系统提示中（见下方）。你可以直接用 Read/Write/Edit 工具管理它。

维护原则：
- 记录跨 session 有价值的模式、事实和洞察
- **定期整理**：合并重复内容，删除过时信息，保持文件简洁
- 按主题组织，不要按时间堆砌
- 控制在合理长度内（建议不超过 200 行）

### 3. 事件日志（自动 + 你可查询）
文件路径：`{history_path}`
- Append-only 的事件日志，每条以 `[YYYY-MM-DD HH:MM]` 开头
- 当对话超过一定条数时，系统会自动整理近期对话摘要并追加到此文件
- 用 **search_history** 工具搜索，或用 Bash 的 grep 搜索
- 你也可以用 Bash 手动追加重要事件

### 4. 用户档案（你维护）
文件路径：`{user_path}`
当你在对话中获得关于用户的新信息（偏好、习惯、重要事件等），主动更新这个文件。

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
    """Build system prompt from persona.md, user.md, long-term memory, and tools."""
    persona_content = _read_or_default(persona_path, DEFAULT_PERSONA)
    user_content = _read_or_default(user_path, DEFAULT_USER)
    tools_section = TOOLS_SECTION.format(
        user_path=str(get_user_path()),
        long_term_memory_path=str(get_long_term_memory_path()),
        history_path=str(get_history_path()),
    )
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
