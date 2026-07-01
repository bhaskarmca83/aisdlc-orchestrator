"""sdlc_orchestrator/agents/confluence_agent.py
ReAct agent: reads a Confluence idea page and extracts structured requirements,
then publishes a full PRD-structured requirements page back to Confluence.
Tools: Atlassian MCP (confluence_search, get_confluence_page, create_confluence_page, ...)
"""
import json
import os
import re
from langchain_core.messages import HumanMessage, SystemMessage

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

After gathering information, respond with ONLY valid JSON (no prose, no markdown fences):
{
  "project_name": "...",
  "tech_stack": ["...", "..."],
  "requirements": ["REQ-1: ...", "REQ-2: ..."],
  "architecture_decisions": [{"id": "ADR-1", "decision": "...", "rationale": "..."}],
  "api_contracts": [{"endpoint": "...", "method": "...", "description": "..."}],
  "code_conventions": {"language": "...", "style": "...", "patterns": ["..."]}
}"""


def _build_prd_body(project_name: str, tech_stack: list, requirements: list,
                     arch_decisions: list, api_contracts: list) -> str:
    req_items = "".join(f"<li>{r}</li>" for r in requirements)
    adr_sections = "".join(
        f"<h3>{d.get('id','ADR')} — {d.get('decision','')}</h3><p><b>Rationale:</b> {d.get('rationale','')}</p>"
        for d in arch_decisions
    )
    api_rows = "".join(
        f"<tr><td>{c.get('method','')}</td><td>{c.get('endpoint','')}</td><td>{c.get('description','')}</td></tr>"
        for c in api_contracts[:10]
    )
    return (
        f"<h1>Product Requirements Document</h1>"
        f"<h2>1. Overview</h2>"
        f"<p><b>Project:</b> {project_name}</p>"
        f"<p><b>Tech Stack:</b> {', '.join(tech_stack)}</p>"
        f"<h2>2. Functional Requirements</h2><ul>{req_items}</ul>"
        f"<h2>3. Architecture Decisions</h2>{adr_sections or '<p>TBD</p>'}"
        f"<h2>4. API Contracts</h2>"
        f"<table><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr>{api_rows}</table>"
        f"<h2>5. Non-Functional Requirements</h2><ul>"
        f"<li>Security: OWASP Top 10 compliance</li>"
        f"<li>Performance: API response &lt; 500ms p95</li>"
        f"<li>Availability: 99.9% uptime SLA</li>"
        f"</ul>"
        f"<h2>6. Out of Scope</h2><p>TBD by development team</p>"
    )


def _parse_mcp_page_id(result) -> str:
    """Three-pass MCP response parser: dict → json.loads → regex."""
    if isinstance(result, dict):
        return result.get("id", "")
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("id"):
                return str(parsed["id"])
        except (json.JSONDecodeError, AttributeError):
            pass
        m = re.search(r'"id"\s*:\s*"(\d+)"', result)
        if m:
            return m.group(1)
    return ""


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

        if tools and page_url:
            fetch_tool = next((t for t in tools if "get" in t.name.lower() and "page" in t.name.lower()), None)
            if fetch_tool:
                emit(EventType.TOOL, "Fetching existing Confluence page")
                try:
                    page_content = await fetch_tool.ainvoke({"url": page_url})
                    user_message = f"Confluence page content:\n{page_content}\n\nIdea text:\n{idea_text}"
                except Exception as e:
                    emit(EventType.ERROR, f"Failed to fetch page: {e}")

        emit(EventType.INFO, "Extracting requirements with LLM")
        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ])
        raw = response.content
        emit(EventType.LLM, f"LLM response: {len(raw)} chars")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            parsed = json.loads(m.group()) if m else {}

        if not parsed.get("requirements"):
            emit(EventType.ERROR, "LLM returned no requirements — using idea text as fallback")
            parsed["requirements"] = [f"REQ-1: {idea_text[:200]}"]

        # Phase 2: create full PRD page in Confluence
        confluence_requirements_page_id = state.get("confluence_requirements_page_id", "")
        if tools and not page_url:
            create_tool = next(
                (t for t in tools if "create" in t.name.lower() and "page" in t.name.lower()), None
            )
            if create_tool:
                space_key   = state.get("target_confluence_space", "").strip() \
                              or os.environ.get("CONFLUENCE_SPACE_KEY", "SD")
                parent_page = os.environ.get("CONFLUENCE_PARENT_PAGE", "524458")
                prd_body    = _build_prd_body(
                    project_name=parsed.get("project_name", state.get("project_name", "Project")),
                    tech_stack=parsed.get("tech_stack", []),
                    requirements=parsed.get("requirements", []),
                    arch_decisions=parsed.get("architecture_decisions", []),
                    api_contracts=parsed.get("api_contracts", []),
                )
                try:
                    result = await create_tool.ainvoke({
                        "space_key": space_key,
                        "parent_id": parent_page,
                        "title":     f"{parsed.get('project_name', state.get('project_name', 'Project'))} — Requirements",
                        "content":   prd_body,
                    })
                    confluence_requirements_page_id = _parse_mcp_page_id(result)
                    emit(EventType.TOOL, f"Created PRD page id={confluence_requirements_page_id}")
                except Exception as e:
                    emit(EventType.ERROR, f"Confluence PRD creation failed: {e}")

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
            "project_name":                   parsed.get("project_name", state.get("project_name", "")),
            "tech_stack":                     parsed.get("tech_stack", []),
            "requirements":                   parsed.get("requirements", []),
            "architecture_decisions":         parsed.get("architecture_decisions", []),
            "api_contracts":                  parsed.get("api_contracts", []),
            "code_conventions":               parsed.get("code_conventions", {}),
            "confluence_page_url":            confluence_requirements_page_id or page_url,
            "confluence_requirements_page_id": confluence_requirements_page_id,
            "current_stage":                  "confluence",
        }
