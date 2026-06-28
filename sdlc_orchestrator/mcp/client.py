"""sdlc_orchestrator/mcp/client.py
Singleton MCP client manager — starts server subprocesses once at app startup,
holds open sessions, and vends LangChain-compatible tools to agents.
"""
import asyncio
from contextlib import AsyncExitStack
from typing import Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_core.tools import BaseTool

from sdlc_orchestrator.mcp.servers import SERVER_CONFIGS, AGENT_TOOL_SERVERS
from sdlc_orchestrator.monitoring.tracker import emit, EventType


class MCPClientManager:
    """
    Manages one persistent ClientSession per MCP server subprocess.
    Lifecycle: start() at FastAPI startup, stop() at shutdown.
    """

    def __init__(self):
        self._stack: Optional[AsyncExitStack] = None
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, list[BaseTool]] = {}
        self._lock = asyncio.Lock()

    async def start(self):
        async with self._lock:
            if self._stack is not None:
                return
            self._stack = AsyncExitStack()
            await self._stack.__aenter__()
            for name, config_fn in SERVER_CONFIGS.items():
                try:
                    config = config_fn()
                    read, write = await self._stack.enter_async_context(
                        stdio_client(config)
                    )
                    session = await self._stack.enter_async_context(
                        ClientSession(read, write)
                    )
                    await session.initialize()
                    tools = await load_mcp_tools(session)
                    self._sessions[name] = session
                    self._tools[name] = tools
                    emit(EventType.INFO, f"MCP server '{name}' started — {len(tools)} tools loaded")
                except Exception as e:
                    emit(EventType.ERROR, f"MCP server '{name}' failed to start: {e}")
                    self._sessions[name] = None
                    self._tools[name] = []

    async def stop(self):
        async with self._lock:
            if self._stack:
                await self._stack.__aexit__(None, None, None)
                self._stack = None
                self._sessions.clear()
                self._tools.clear()

    def get_tools_for_agent(self, agent_stage: str) -> list[BaseTool]:
        """Return the union of tools from all servers the agent is allowed to use."""
        server_names = AGENT_TOOL_SERVERS.get(agent_stage, [])
        tools: list[BaseTool] = []
        for name in server_names:
            tools.extend(self._tools.get(name, []))
        return tools

    def get_tools(self, *server_names: str) -> list[BaseTool]:
        tools: list[BaseTool] = []
        for name in server_names:
            tools.extend(self._tools.get(name, []))
        return tools

    @property
    def available_servers(self) -> list[str]:
        return [name for name, session in self._sessions.items() if session is not None]


# Global singleton — imported by agents and api/main.py
mcp_manager = MCPClientManager()
