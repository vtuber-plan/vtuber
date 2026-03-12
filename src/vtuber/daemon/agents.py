"""Agent factory — centralized creation of Claude SDK agents."""

import asyncio
import logging
from collections import OrderedDict

from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server
from claude_agent_sdk.types import AgentDefinition, ClaudeAgentOptions

from vtuber.config import ensure_workspace_dir, get_config, get_persona_path, get_user_path, get_plugins_dir
from vtuber.permissions import agent_permission_handler
from vtuber.persona import build_system_prompt

logger = logging.getLogger("vtuber.daemon")

_SERVER_NAME = "vtuber"
# Tools that should only be used by the web-researcher sub-agent, not the main agent.
_WEB_ONLY_TOOLS = {"web_search", "web_fetch"}


def create_tools_server(include_schedule: bool = True):
    """Create an SDK MCP server with vtuber tools.

    Returns:
        (server, all_tool_names, web_tool_names) tuple.
    """
    from vtuber.tools.memory import search_sessions, list_sessions, read_session
    from vtuber.tools.web import web_search, web_fetch
    from vtuber.tools.lifecycle import agent_restart

    tools = [search_sessions, list_sessions, read_session, web_search, web_fetch, agent_restart]

    if include_schedule:
        from vtuber.tools.schedule import schedule_create, schedule_list, schedule_cancel
        tools.extend([schedule_create, schedule_list, schedule_cancel])

    all_names = [f"mcp__{_SERVER_NAME}__{i.name}" for i in tools]
    web_names = [f"mcp__{_SERVER_NAME}__{i.name}" for i in tools if i.name in _WEB_ONLY_TOOLS]
    server = create_sdk_mcp_server(_SERVER_NAME, tools=tools)
    return server, all_names, web_names


def build_agent_options(
    *,
    system_prompt: str | None = None,
    prompt_suffix: str = "",
    include_preset_system_prompt: bool = False,
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
        "can_use_tool": agent_permission_handler,
        "cli_path": get_config().cli_path,
        "cwd": str(ensure_workspace_dir()),
    }

    # Load plugins from ~/.vtuber/plugins/
    plugins_dir = get_plugins_dir()
    if plugins_dir.is_dir():
        plugin_configs = [
            {"type": "local", "path": str(p)}
            for p in sorted(plugins_dir.iterdir())
            if p.is_dir() and not p.name.startswith(("_", "."))
        ]
        if plugin_configs:
            options_kwargs["plugins"] = plugin_configs

    if include_preset_system_prompt:
        options_kwargs['system_prompt'] = {
            "type": "preset",
            "preset": "claude_code",
            "append": system_prompt,
        }

    if include_mcp_tools:
        tools_server, all_tool_names, web_tool_names = create_tools_server(
            include_schedule=include_schedule,
        )
        options_kwargs["mcp_servers"] = {"vtuber": tools_server}
        # Main agent cannot use web tools directly — delegate via web-researcher sub-agent.
        options_kwargs["allowed_tools"] = [t for t in all_tool_names if t not in web_tool_names]
        options_kwargs["agents"] = {
            "web-researcher": AgentDefinition(
                description=(
                    "Use this agent for ANY task that requires web searching or fetching web pages. "
                    "This agent has access to web_search and web_fetch tools and will return concise, summarized results. "
                    "Always delegate web research to this agent instead of calling web tools directly."
                ),
                prompt=(
                    "You are a web research assistant. "
                    "Use the web_search and web_fetch tools to find information as requested. "
                    "After gathering results, provide a concise summary focusing only on the most relevant information. "
                    "Keep your response brief and focused. Do not include unnecessary preamble."
                ),
                tools=web_tool_names,
            ),
        }

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


class AgentPool:
    """Manages a pool of ClaudeSDKClient instances, one per session_id.

    Agents are lazily created on first access and evicted LRU when the pool
    reaches ``max_agents``.  Different session types (e.g. private vs group)
    can use different agent configurations via *profiles*.
    """

    def __init__(
        self,
        max_agents: int = 5,
        profiles: dict[str, dict] | None = None,
    ):
        self._agents: OrderedDict[str, ClaudeSDKClient] = OrderedDict()
        self._max = max_agents
        self._profiles: dict[str, dict] = profiles or {"private": {}}

    async def get(self, session_id: str, profile: str = "private") -> ClaudeSDKClient:
        """Get or create an agent for *session_id*.  LRU eviction when full."""
        if session_id in self._agents:
            self._agents.move_to_end(session_id)
            return self._agents[session_id]

        # Evict oldest if at capacity
        if len(self._agents) >= self._max:
            oldest_id, oldest_agent = self._agents.popitem(last=False)
            await safe_disconnect(oldest_agent)
            logger.info("Evicted agent for session %s", oldest_id)

        kwargs = self._profiles.get(profile, self._profiles.get("private", {}))
        agent = await create_agent(**kwargs)
        self._agents[session_id] = agent
        logger.info(
            "Created %s agent for session %s (pool=%d/%d)",
            profile, session_id, len(self._agents), self._max,
        )
        return agent

    async def remove(self, session_id: str) -> None:
        """Remove and disconnect a specific agent."""
        if agent := self._agents.pop(session_id, None):
            await safe_disconnect(agent)

    async def close_all(self) -> None:
        """Disconnect all agents gracefully."""
        for agent in self._agents.values():
            await safe_disconnect(agent)
        self._agents.clear()

    async def kill_and_recreate(
        self, session_id: str, profile: str = "private",
    ) -> ClaudeSDKClient:
        """Kill a hung agent and create a fresh one for the session."""
        from vtuber.daemon.agent_query import kill_agent_process

        if agent := self._agents.pop(session_id, None):
            kill_agent_process(agent)
        return await self.get(session_id, profile=profile)

    def kill_all_and_clear(self) -> None:
        """Kill all agent subprocesses immediately (for reload/recovery)."""
        from vtuber.daemon.agent_query import kill_agent_process

        for agent in self._agents.values():
            kill_agent_process(agent)
        self._agents.clear()
