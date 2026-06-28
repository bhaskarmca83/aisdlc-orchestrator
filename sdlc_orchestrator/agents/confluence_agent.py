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

        # Phase 1: fetch existing page if URL provided, else extract from idea text
        if tools and page_url:
            fetch_tool = next((t for t in tools if "get" in t.name.lower() and "page" in t.name.lower()), None)
            if fetch_tool:
                emit(EventType.TOOL, "Fetching existing Confluence page")
                try:
                    page_content = await fetch_tool.ainvoke({"url": page_url})
                    user_message = f"Confluence page content:\n{page_content}\n\nIdea text:\n{idea_text}"
                except Exception as e:
                    emit(EventType.ERROR, f"Failed to fetch page: {e}")

        emit(EventType.INFO, "Extracting requirements from idea text with LLM")
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

        # Phase 2: create requirements page in Confluence if not already there
        confluence_page_id = parsed.get("confluence_page_id", "")
        if tools and not page_url:
            create_tool = next(
                (t for t in tools if "create" in t.name.lower() and "page" in t.name.lower()), None
            )
            if create_tool:
                req_body = "\n".join(f"<li>{r}</li>" for r in parsed.get("requirements", []))
                try:
                    result = await create_tool.ainvoke({
                        "space_key":  "SD",
                        "parent_id":  "50200578",
                        "title":      f"{parsed.get('project_name', state.get('project_name', 'Project'))} — Requirements",
                        "content":    f"<h2>Requirements</h2><ul>{req_body}</ul>",
                    })
                    confluence_page_id = result.get("id", "") if isinstance(result, dict) else ""
                    emit(EventType.TOOL, f"Created Confluence requirements page id={confluence_page_id}")
                except Exception as e:
                    emit(EventType.ERROR, f"Confluence page creation failed: {e}")

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
            "confluence_page_url":    confluence_page_id or page_url,
            "current_stage":          "confluence",
        }
