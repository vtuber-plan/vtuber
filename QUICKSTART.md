# VTuber 快速启动指南

## 安装

```bash
# 安装依赖
uv sync

# 或使用 pip
pip install -e .
```

## 首次使用

### 1. 启动守护进程（会自动运行引导）

```bash
vtuber start
```

首次运行时，系统会自动启动交互式引导，询问你：

**第一步：设置助手人格**
- 你希望助手叫什么名字？
- 你希望助手有什么性格特点？
- 你希望助手的说话风格是怎样的？

**第二步：设置你的信息**
- 你希望助手怎么称呼你？
- 你的职业或兴趣是什么？

引导完成后，配置文件会保存在 `~/.vtuber/` 目录。

### 2. 检查守护进程状态

```bash
vtuber status
```

输出示例：
```
Daemon is running (PID: 12345)
Socket: /Users/you/.vtuber/daemon.sock
Socket file exists
Socket connection: OK
```

### 3. 开始对话

```bash
vtuber chat
```

现在你可以和你的数字生命助手对话了！

```
Connected to daemon at /Users/you/.vtuber/daemon.sock
Type your message and press Enter. Type /quit or /exit to quit.

> 你好！
你好！很高兴见到你！我是你的数字生命助手。有什么我可以帮助你的吗？

> 记住我的生日是3月15日
Memorized: 生日 = 3月15日

> 我的生日是什么时候？
你的生日是3月15日

> /quit
Goodbye!
```

## 日常使用

### 启动守护进程
```bash
vtuber start
```

### 停止守护进程
```bash
vtuber stop
```

### 重启守护进程
```bash
vtuber restart
```

### 检查状态
```bash
vtuber status
```

### 开始对话
```bash
vtuber chat
```

## 高级功能

### 1. 创建定时提醒

在对话中使用 `schedule_create` 工具：

```
> 提醒我每天早上9点喝水
我会创建一个每天早上9点的提醒任务。
Created scheduled task 'daily_water_reminder': 每天早上9点提醒喝水
```

### 2. 查看所有定时任务

```
> 列出所有定时任务
Scheduled tasks:
- daily_water_reminder: 每天早上9点提醒喝水 (next: 2026-03-06 09:00:00)
- weekly_report: 每周一总结本周任务 (next: 2026-03-09 10:00:00)
```

### 3. 取消定时任务

```
> 取消喝水提醒
Cancelled task 'daily_water_reminder'
```

### 4. 记忆管理

```
> 记住我最喜欢的颜色是蓝色
Memorized: 最喜欢的颜色 = 蓝色

> 我最喜欢的颜色是什么？
你最喜欢的颜色是蓝色

> 列出所有记忆
{
  "生日": "3月15日",
  "最喜欢的颜色": "蓝色"
}

> 忘记我最喜欢的颜色
Forgot: 最喜欢的颜色
```

## 配置文件

所有配置文件位于 `~/.vtuber/` 目录：

- **persona.md** - 助手人格配置
- **user.md** - 用户信息
- **heartbeat.md** - 心跳任务清单
- **vtuber.db** - SQLite 数据库（定时任务）
- **memory/global.json** - 持久化记忆

### 编辑人格配置

```bash
nano ~/.vtuber/persona.md
```

示例：
```markdown
# Persona Configuration

## Basic Info
- Name: 小助手
- Description: 一个友好的数字生命助手

## Personality Traits
- 友好和亲切
- 好奇心强
- 乐于助人
- 幽默风趣

## Speaking Style
- 随意和温暖
- 偶尔使用表情符号
- 简洁明了
```

修改后重启守护进程：
```bash
vtuber restart
```

## 心跳机制

守护进程每 5 分钟会自动向 agent 发送心跳消息。你可以编辑 `~/.vtuber/heartbeat.md` 来定义心跳任务：

```markdown
# 心跳任务清单

## 检查任务
- 检查是否有即将到期的任务
- 检查是否需要提醒用户重要事项

## 主动行为
- 如果长时间没有互动，主动问候用户
- 如果发现有趣的信息，分享给用户

## 自维护
- 清理过期的记忆
- 总结本周的活动
```

Agent 会根据这个清单决定在心跳时做什么。

## 多客户端支持

守护进程支持多个客户端同时连接。你可以：

1. 在一个终端窗口运行 `vtuber chat`
2. 在另一个终端窗口也运行 `vtuber chat`
3. 两个客户端都能收到 agent 的响应

这对于测试和多用户场景很有用。

## 故障排除

### Daemon 无法启动

```bash
# 检查是否有残留进程
vtuber status

# 如果显示运行但无法连接
vtuber stop

# 清理 socket 文件
rm ~/.vtuber/daemon.sock

# 重新启动
vtuber start
```

### 连接被拒绝

```bash
# 检查守护进程是否运行
vtuber status

# 如果没有运行
vtuber start
```

### 配置文件损坏

```bash
# 备份当前配置
cp -r ~/.vtuber ~/.vtuber.backup

# 删除损坏的文件
rm ~/.vtuber/persona.md
rm ~/.vtuber/user.md

# 重新运行引导
vtuber restart
```

### 查看日志

目前守护进程输出到 stdout。要查看日志：

```bash
# 前台运行（调试模式）
vtuber-daemon

# 或查看进程输出
ps aux | grep vtuber
```

## 环境变量

确保设置了以下环境变量：

```bash
export ANTHROPIC_API_KEY="your-api-key"
export ANTHROPIC_BASE_URL="https://open.bigmodel.cn/api/anthropic"
export ANTHROPIC_MODEL="glm-4.7"
```

## 开发和测试

```bash
# 运行测试
uv run pytest tests/ -v

# 验证代码
uv run python -m py_compile src/vtuber/**/*.py

# 导入测试
uv run python -c "from vtuber.daemon.server import DaemonServer; print('OK')"
```

## 获取帮助

```bash
vtuber --help
```

或在对话中询问助手：
```
> 你能做什么？
我可以帮助你：
- 记住重要信息（使用 memorize/recall/forget）
- 创建定时提醒（使用 schedule_create/list/cancel）
- 回答问题和提供建议
- 进行日常对话

有什么我可以帮助你的吗？
```

## 提示和技巧

1. **使用记忆功能**: 让助手记住重要的信息，这样即使重启也能保持上下文。

2. **设置定时任务**: 为日常事项创建提醒，比如喝水、休息、检查邮件等。

3. **编辑人格配置**: 根据你的喜好调整助手的性格和说话风格。

4. **使用心跳任务**: 在 `heartbeat.md` 中定义你希望助手主动做的事情。

5. **多终端使用**: 可以同时打开多个 `vtuber chat` 会话，所有会话都会收到响应。

6. **优雅退出**: 使用 `/quit` 或 `/exit` 命令退出客户端，或按 Ctrl+D。

## 享受你的数字生命助手！

🎉 现在你已经了解了所有功能，开始享受与你的数字生命助手的互动吧！

如果遇到问题，请检查故障排除部分或查看项目文档。
