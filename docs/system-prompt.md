# System Prompt 构建

本文档描述智能体的 system prompt 是如何构建的，以及各组成部分的作用。

## 构建流程

`persona.py:build_system_prompt()` 将以下部分用 `---` 分隔拼接：

```
[persona.md]        ← AI 人格设定
---
[user.md]           ← 用户档案
---
[TOOLS_SECTION]     ← 框架能力说明（硬编码）
---
[MEMORY.md]         ← 长期记忆（如果存在）
```

## 各部分来源

| 部分 | 文件路径 | 说明 |
|:--|:--|:--|
| Persona | `~/.vtuber/persona.md` | 用户在 onboarding 时设定，可随时修改 |
| User | `~/.vtuber/user.md` | 用户档案，同上 |
| Tools Section | `src/vtuber/persona.py` 中的 `TOOLS_SECTION` 常量 | 框架能力描述，开发者维护 |
| Long-term Memory | `~/.vtuber/memory/MEMORY.md` | 智能体在运行中自行更新 |

若 persona.md / user.md 不存在，则使用 `templates.py` 中的 `DEFAULT_PERSONA` / `DEFAULT_USER`。

## TOOLS_SECTION 内容

TOOLS_SECTION 是智能体了解自身能力的核心。包含以下段落：

### Memory System
- MEMORY.md 的用途和更新时机
- `search_sessions` 工具的用法（summary / detailed 两种模式）
- 自动合并（auto-consolidation）机制说明

### Web Research
- 必须委托 `web-researcher` sub-agent，不得直接调用 web 工具

### Environment
框架环境描述，包括：

- **Key Paths** — 所有关键路径（config、persona、user、workspace、plugins、memory、heartbeat）
- **Plugins** — 插件目录结构、安装/卸载方式、如何生效（调用 `agent_restart`）
- **Schedule Tools** — `schedule_create`、`schedule_list`、`schedule_cancel`
- **Lifecycle Tools** — `agent_restart`（重启自己）

## MCP Tools 注册

`daemon/agents.py:create_tools_server()` 注册所有 MCP tools：

```python
tools = [
    # memory
    search_sessions, list_sessions, read_session,
    # web (仅 web-researcher sub-agent 可用)
    web_search, web_fetch,
    # lifecycle
    agent_restart,
    # schedule (可选)
    schedule_create, schedule_list, schedule_cancel,
]
```

其中 `web_search` / `web_fetch` 通过 `allowed_tools` 限制仅 web-researcher sub-agent 可用，主 agent 不可直接调用。

## agent_restart 工作流

```
agent 调用 agent_restart tool
  → tool 设置 asyncio.Event 信号，返回提示文字
  → 当前 query 正常结束
  → daemon (_dispatch_to_agent) 检测 consume_restart() 为 True
  → 调用 agent_pool.kill_and_recreate(session_id)
  → 该 session 的 agent 进程被终止并重建
```

这确保了重启发生在 query 完成之后，不会中断当前响应的传输。

## 如何修改

- **添加新 tool**：在 `tools/` 下新建模块 → 在 `create_tools_server()` 中注册 → 如需要在 TOOLS_SECTION 中补充说明
- **修改人格/用户默认值**：编辑 `templates.py`
- **修改框架描述**：编辑 `persona.py` 中的 `TOOLS_SECTION`
