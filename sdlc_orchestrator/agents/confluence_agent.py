"""sdlc_orchestrator/agents/confluence_agent.py
ReAct agent: reads a Confluence idea page and extracts structured requirements.
Tools: Atlassian MCP (confluence_search, get_confluence_page, ...)
"""
import json
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.mcp.client import mcp_manager

SYSTEM_PROMPT = """You are a Senior Business Analyst with access to Confluence tools.

Your task:
1. If a confluence_page_url is provided, use your tools to fetch the page content
2. Otherwise use the idea_raw text directly
3. Extract structured requirements from the idea

After gathering information, respond with ONLY valid JSON:
{
  "project_name": "...",
  "tech_stack": ["...", "..."],
  "requirements": ["REQ-1: ...", "REQ-2: ..."],
  "architecture_decisions": [{"id": "ADR-1", "decision": "...", "rationale": "..."}],
  "api_contracts": [{"endpoint": "...", "method": "...", "description": "..."}],
  "code_conventions": {"language": "...", "style": "...", "patterns": ["..."]}
}"""


async def confluence_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("confluence", state):
        emit(EventType.INFO, "ConfluenceAgent starting")

        mem = SharedMemory(state["project_id"])
        await mem.init()
        ctx       = await mem.get_project_context()
        learnings = await mem.get_accumulated_learnings(limit=5)

        llm   = ProviderFactory.get_model(AgentRole.CONFLUENCE)
        tools = mcp_manager.get_tools_for_agent("confluence")

        idea_text    = state.get("idea_raw", "")
        page_url     = state.get("confluence_page_url", "")
        learning_ctx = json.dumps([l["content"] for l in learnings[:3]], indent=2) if learnings else ""

        user_message = (
            f"Confluence page URL: {page_url}\n" if page_url else ""
        ) + f"Idea text:\n{idea_text}\n\n" + (
            f"Previous learnings:\n{learning_ctx}" if learning_ctx else ""
        )

        if tools:
            emit(EventType.TOOL, f"Using {len(tools)} Atlassian MCP tools")
            agent    = create_react_agent(llm, tools)
            response = await agent.ainvoke({
                "messages": [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_message),
                ]
            })
            raw = response["messages"][-1].content
        else:
            emit(EventType.INFO, "No Atlassian MCP tools — using LLM only")
            response = await llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            raw = response.content

        emit(EventType.LLM, f"LLM response: {len(raw)} chars")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            parsed = json.loads(m.group()) if m else {}

        await mem.update_project_context({
            "project_name":           parsed.get("project_name", state.get("project_name", "")),
            "tech_stack":             parsed.get("tech_stack", []),
            "requirements":           parsed.get("requirements", []),
            "architecture_decisions": parsed.get("architecture_decisions", []),
            "api_contracts":          parsed.get("api_contracts", []),
            "code_conventions":       parsed.get("code_conventions", {}),
        })

        await mem.save_story_learning({
            "story_id": state.get("current_story_id", "init"), "agent_name": "confluence",
            "learning_type": "requirements_extracted",
            "content": {"requirement_count": len(parsed.get("requirements", []))}, "metadata": {},
        })

        emit(EventType.DONE, f"Extracted {len(parsed.get('requirements', []))} requirements")
        return {
            **state,
            "project_name":           parsed.get("project_name", state.get("project_name", "")),
            "tech_stack":             parsed.get("tech_stack", []),
            "requirements":           parsed.get("requirements", []),
            "architecture_decisions": parsed.get("architecture_decisions", []),
            "api_contracts":          parsed.get("api_contracts", []),
            "code_conventions":       parsed.get("code_conventions", {}),
            "current_stage":          "confluence",
        }
