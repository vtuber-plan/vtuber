# Onboarding Redesign: AI 自主写入配置文件

## 问题

当前 onboarding 流程：
- AI 没有文件编辑权限，只生成文本，Python 端解析后写入
- 解析逻辑脆弱（依赖 `Name:`, `Traits:` 等固定格式）
- 流程死板（固定两步、固定问题）
- 格式不对就 fallback 到默认模板，等于白问

## 设计

### 核心思路

让 onboarding agent 直接拥有文件读写能力，通过 system prompt 引导 AI 自己决定向 `persona.md` 和 `user.md` 写入什么内容。Python 端只负责流程控制和权限校验。

### 两阶段流程

```
阶段1: 用户自我介绍 → AI 总结 → 用户确认/修改 → AI 写入 ~/.vtuber/user.md
阶段2: AI 设定描述 → AI 总结 → 用户确认/修改 → AI 写入 ~/.vtuber/persona.md
```

每阶段内循环：
1. Python 打印提示文本，等待用户输入
2. 发送给 AI（含指导 prompt）
3. AI 总结呈现给用户
4. 用户确认 → AI 写入文件 → 进入下一阶段
5. 用户有修改意见 → 转发给 AI → AI 重新总结 → 回到步骤 4

### 权限控制

使用 SDK 的 `can_use_tool` 回调做细粒度控制，不使用 `bypassPermissions`：

```python
async def _onboarding_permission(tool_name, tool_input, context):
    allowed_files = {
        str(Path.home() / ".vtuber" / "persona.md"),
        str(Path.home() / ".vtuber" / "user.md"),
    }
    if tool_name in ("Write", "Edit", "MultiEdit"):
        if tool_input.get("file_path", "") not in allowed_files:
            return PermissionResultDeny(message="只允许写入 persona.md 和 user.md")
    if tool_name == "Read":
        if tool_input.get("file_path", "") not in allowed_files:
            return PermissionResultDeny(message="只允许读取 persona.md 和 user.md")
    return PermissionResultAllow()
```

### System Prompt

告诉 AI：
1. 你是初次设置引导助手
2. `~/.vtuber/user.md` — 存储用户信息（称呼、职业、兴趣、偏好等）
3. `~/.vtuber/persona.md` — 存储 AI 人格设定（名字、性格、说话风格、背景设定等）
4. 提供默认模板作为参考格式
5. 根据用户输入总结并呈现，得到确认后用 Write 工具写入文件

### 允许的工具

仅允许：`Read`, `Write`, `Edit`, `MultiEdit`（且路径受 `can_use_tool` 限制）

### 退出条件

每阶段的退出条件：检测到对应文件已被 AI 写入（`Path.exists()`）。

### 需要删除的代码

- `_ask_about_persona`, `_ask_about_user` — 不再需要固定问题
- `_save_persona`, `_save_user` — AI 自己写文件
- 所有格式解析逻辑（`Name:`, `Traits:` 解析等）

### 保留的代码

- `check_and_run_onboarding()` — 入口检查逻辑不变
- `create_default_configs()` — daemon 启动时的 fallback 不变
- `_extract_stream_text()` — 流式输出提取仍需要
