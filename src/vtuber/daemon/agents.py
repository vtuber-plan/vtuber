"""Agent factory — centralized creation of Claude SDK agents."""

import asyncio
import logging

from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server
from claude_agent_sdk.types import ClaudeAgentOptions

from vtuber.config import ensure_workspace_dir, get_config, get_persona_path, get_user_path
from vtuber.persona import build_system_prompt

logger = logging.getLogger("vtuber.daemon")


def create_tools_server(include_schedule: bool = True):
    """Create an SDK MCP server with vtuber tools.

    Returns:
        (server, allowed_tool_names) tuple.
    """
    SERVER_NAME = "vtuber_tools"

    from vtuber.tools.memory import search_sessions, list_sessions, read_session

    tools = [search_sessions, list_sessions, read_session]
    allowed = [
        f"mcp__{SERVER_NAME}__search_sessions",
        f"mcp__{SERVER_NAME}__list_sessions",
        f"mcp__{SERVER_NAME}__read_session",
    ]

    if include_schedule:
        from vtuber.tools.schedule import schedule_create, schedule_list, schedule_cancel

        tools.extend([schedule_create, schedule_list, schedule_cancel])
        allowed.extend([
            f"mcp__{SERVER_NAME}__schedule_create",
            f"mcp__{SERVER_NAME}__schedule_list",
            f"mcp__{SERVER_NAME}__schedule_cancel",
        ])

    server = create_sdk_mcp_server(SERVER_NAME, tools=tools)
    return server, allowed


def build_agent_options(
    *,
    system_prompt: str | None = None,
    prompt_suffix: str = "",
    include_preset_system_prompt: bool = True,
    include_schedule: bool = False,
    include_mcp_tools: bool = True,
    include_preset_tools: bool = False,
    session_persistence: bool = False,
    resume: bool = False,
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions for use with create_agent() or sdk query().

    Args:
        system_prompt: Custom system prompt. If None, auto-builds from persona + user.
        prompt_suffix: Text appended to the auto-built persona prompt (ignored if system_prompt is set).
        include_preset_system_prompt: Wrap system prompt in claude_code preset.
        include_schedule: Include schedule tools in the MCP server.
        include_mcp_tools: Include the MCP tool server at all.
        include_preset_tools: Include Claude Code preset tools.
        session_persistence: Allow Claude session persistence (default: disabled).
        resume: Resume an existing agent session.
    """
    if system_prompt is None:
        system_prompt = build_system_prompt(get_persona_path(), get_user_path())
    if prompt_suffix:
        system_prompt = f"{system_prompt}\n\n{prompt_suffix}"

    options_kwargs: dict = {
        "system_prompt": system_prompt,
        "permission_mode": "bypassPermissions",
        "cli_path": get_config().cli_path,
        "cwd": str(ensure_workspace_dir()),
    }

    if include_preset_system_prompt:
        options_kwargs['system_prompt'] = {
            "type": "preset",
            "preset": "claude_code",
            "append": system_prompt,
        }

    if include_mcp_tools:
        tools_server, allowed_tools = create_tools_server(
            include_schedule=include_schedule,
        )
        options_kwargs["mcp_servers"] = {"vtuber_tools": tools_server}
        options_kwargs["allowed_tools"] = allowed_tools

    if include_preset_tools:
        options_kwargs["tools"] = {"type": "preset", "preset": "claude_code"}

    if resume:
        options_kwargs["resume"] = True

    if not session_persistence:
        options_kwargs["extra_args"] = {"no-session-persistence": None}

    return ClaudeAgentOptions(**options_kwargs)


async def create_agent(
    *,
    system_prompt: str | None = None,
    prompt_suffix: str = "",
    include_preset_system_prompt: bool = True,
    include_schedule: bool = False,
    include_mcp_tools: bool = True,
    include_preset_tools: bool = False,
    session_persistence: bool = False,
    resume: bool = False,
) -> ClaudeSDKClient:
    """Create and connect a persistent Claude SDK agent.

    For one-shot queries, use build_agent_options() + sdk query() instead.
    """
    options = build_agent_options(
        system_prompt=system_prompt,
        prompt_suffix=prompt_suffix,
        include_preset_system_prompt=include_preset_system_prompt,
        include_schedule=include_schedule,
        include_mcp_tools=include_mcp_tools,
        include_preset_tools=include_preset_tools,
        session_persistence=session_persistence,
        resume=resume,
    )
    agent = ClaudeSDKClient(options)
    await agent.connect()
    return agent


async def safe_disconnect(agent: ClaudeSDKClient, timeout: float = 5.0) -> None:
    """Disconnect an agent safely with a timeout."""
    try:
        await asyncio.wait_for(agent.disconnect(), timeout=timeout)
    except Exception:
        pass


class GroupAgentManager:
    """Manages per-channel persistent agents for group chats."""

    def __init__(self):
        self._agents: dict[str, ClaudeSDKClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._create_lock = asyncio.Lock()

    async def get_or_create(self, channel_id: str) -> ClaudeSDKClient:
        """Get an existing agent for a channel, or create a new one (thread-safe)."""
        if channel_id in self._agents:
            return self._agents[channel_id]

        async with self._create_lock:
            # Double-check after acquiring lock
            if channel_id in self._agents:
                return self._agents[channel_id]

            agent = await create_agent(
                prompt_suffix=(
                    f"你正在参与一个群聊（频道: {channel_id}）。\n"
                    "你会收到群里最近的对话消息。请根据对话内容决定是否需要回复。\n"
                    "如果对话不需要你参与，请只回复: NO_RESPONSE\n"
                    "如果需要回复，直接回复内容即可，不要加任何前缀。"
                ),
            )
            self._agents[channel_id] = agent
            self._locks[channel_id] = asyncio.Lock()
            logger.info("[group/%s] created persistent agent", channel_id)
            return agent

    def get_lock(self, channel_id: str) -> asyncio.Lock:
        """Get the concurrency lock for a channel's agent."""
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    async def recover(self, channel_id: str) -> None:
        """Kill and recreate the agent for a channel."""
        from vtuber.daemon.streaming import kill_agent_process

        old = self._agents.pop(channel_id, None)
        if old:
            kill_agent_process(old)
            await safe_disconnect(old)
            logger.warning("[group/%s] agent recovered", channel_id)

    async def close_all(self):
        """Disconnect all group agents."""
        for channel_id, agent in self._agents.items():
            try:
                await agent.disconnect()
                logger.info("[group/%s] agent disconnected", channel_id)
            except Exception:
                pass
        self._agents.clear()
        self._locks.clear()
