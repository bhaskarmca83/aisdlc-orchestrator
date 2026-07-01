"""sdlc_orchestrator/agents/design_agent.py
ReAct agent: produces architecture artifacts and publishes a full TSD to Confluence.
Tools: Atlassian MCP (create_confluence_page, update_confluence_page, ...)
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
from sdlc_orchestrator.agents._utils import find_mcp_tool, parse_mcp_id

SYSTEM_PROMPT = """You are a Senior Solutions Architect.

Design the technical architecture for the stories provided.
Respond with ONLY valid JSON (no prose, no markdown fences):

{
  "architecture_diagram": "Mermaid flowchart string",
  "api_design": [{"endpoint": "...", "method": "GET|POST|PUT|DELETE", "request": {}, "response": {}, "auth": true, "description": "..."}],
  "db_schema": [{"table": "...", "columns": [{"name": "...", "type": "...", "constraints": "..."}]}],
  "component_breakdown": [{"component": "...", "responsibility": "...", "tech": "..."}],
  "security_notes": ["..."],
  "performance_notes": ["..."]
}"""


def _col_text(col: dict) -> str:
    return " ".join(filter(None, [col.get("name", ""), col.get("type", ""), col.get("constraints", "")]))


def _build_tsd_body(artifacts: dict) -> str:
    api_rows = "".join(
        f"<tr><td>{e.get('method','')}</td><td>{e.get('endpoint','')}</td><td>{e.get('description','')}</td></tr>"
        for e in artifacts.get("api_design", [])[:15]
    )
    db_rows = "".join(
        f"<tr><td><b>{t.get('table','')}</b></td><td>{'<br/>'.join(_col_text(c) for c in t.get('columns', []))}</td></tr>"
        for t in artifacts.get("db_schema", [])[:10]
    )
    comp_rows = "".join(
        f"<tr><td><b>{c.get('component','')}</b></td><td>{c.get('responsibility','')}</td><td>{c.get('tech','')}</td></tr>"
        for c in artifacts.get("component_breakdown", [])[:10]
    )
    return (
        f"<h1>Technical Design Document</h1>"
        f"<h2>1. Architecture Overview</h2><pre>{artifacts.get('architecture_diagram','')}</pre>"
        f"<h2>2. Component Breakdown</h2>"
        f"<table><tr><th>Component</th><th>Responsibility</th><th>Technology</th></tr>{comp_rows}</table>"
        f"<h2>3. API Design</h2>"
        f"<table><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr>{api_rows}</table>"
        f"<h2>4. Database Schema</h2>"
        f"<table><tr><th>Table</th><th>Columns</th></tr>{db_rows}</table>"
        f"<h2>5. Security Notes</h2><ul>{''.join(f'<li>{n}</li>' for n in artifacts.get('security_notes',[]))}</ul>"
        f"<h2>6. Performance Notes</h2><ul>{''.join(f'<li>{n}</li>' for n in artifacts.get('performance_notes',[]))}</ul>"
    )


async def design_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("design", state):
        emit(EventType.INFO, "DesignAgent starting")

        mem = SharedMemory(state["project_id"])
        await mem.init()
        learnings = await mem.get_accumulated_learnings(limit=5)

        llm   = ProviderFactory.get_model(AgentRole.DESIGN)
        tools = mcp_manager.get_tools_for_agent("design")

        stories = state.get("stories", [])
        if not stories:
            raise ValueError("No stories — run story agent first")

        stories_text = "\n".join(
            f"- [{s.get('jira_key','N/A')}] {s.get('summary','')} | tags: {s.get('tags',[])} | AC: {'; '.join(s.get('acceptance_criteria',[]))}"
            for s in stories
        )
        learning_ctx = json.dumps([l["content"] for l in learnings[:3]], indent=2) if learnings else ""

        user_message = (
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
            f"Existing ADRs:\n{json.dumps(state.get('architecture_decisions', []), indent=2)}\n\n"
            f"Stories:\n{stories_text}\n"
            + (f"\nPrevious learnings:\n{learning_ctx}" if learning_ctx else "")
        )

        # Inject revision reason if architect previously rejected
        revision_reason = state.get("arch_revision_reason", "")
        if revision_reason:
            user_message = (
                f"PREVIOUS TSD REJECTED by Architect: {revision_reason}\n"
                f"Please revise the design to address these concerns.\n\n"
            ) + user_message

        emit(EventType.INFO, "Generating design artifacts with LLM")
        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ])
        raw = response.content

        try:
            artifacts = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            artifacts = json.loads(m.group()) if m else {}

        if not artifacts.get("api_design") and not artifacts.get("component_breakdown"):
            emit(EventType.ERROR, "LLM returned empty design artifacts — design may be incomplete")

        # Phase 2: publish full TSD to Confluence
        tsd_page_id = state.get("confluence_tsd_page_id", "")
        if tools:
            create_tool = next(
                (t for t in tools if "create" in t.name.lower() and "page" in t.name.lower()), None
            )
            if create_tool:
                space_key    = state.get("target_confluence_space", "").strip() \
                               or os.environ.get("CONFLUENCE_SPACE_KEY", "SD")
                parent_page  = os.environ.get("CONFLUENCE_PARENT_PAGE", "524458")
                project_name = state.get("project_name", "Project")
                tsd_body     = _build_tsd_body(artifacts)
                try:
                    result = await create_tool.ainvoke({
                        "space_key": space_key,
                        "parent_id": parent_page,
                        "title":     f"{project_name} — Technical Design",
                        "content":   tsd_body,
                    })
                    tsd_page_id = parse_mcp_id(result)
                    emit(EventType.TOOL, f"Created TSD page id={tsd_page_id}")
                except Exception as e:
                    emit(EventType.ERROR, f"Confluence TSD creation failed: {e}")

            # Link TSD to first Jira story via comment
            if tsd_page_id and state.get("stories"):
                conf_base       = os.environ.get("CONFLUENCE_BASE_URL", "https://bhaskarwork.atlassian.net/wiki")
                first_story_key = state["stories"][0].get("jira_key", "")
                comment_tool    = next(
                    (t for t in tools if "comment" in t.name.lower() and "jira" in t.name.lower()), None
                )
                if first_story_key and comment_tool:
                    try:
                        await comment_tool.ainvoke({
                            "issue_key": first_story_key,
                            "body":      f"Technical Design Document: {conf_base}/spaces/{space_key}/pages/{tsd_page_id}",
                        })
                        emit(EventType.TOOL, f"Linked TSD to Jira story {first_story_key}")
                    except Exception as e:
                        emit(EventType.ERROR, f"Jira TSD comment failed: {e}")

        await mem.update_project_context({
            "api_contracts": artifacts.get("api_design", state.get("api_contracts", [])),
            "db_schema":     artifacts.get("db_schema", []),
        })
        await mem.save_story_learning({
            "story_id": state.get("current_story_id", ""), "agent_name": "design",
            "learning_type": "design_artifacts",
            "content": {"endpoints": len(artifacts.get("api_design", []))}, "metadata": {},
        })

        emit(EventType.DONE, "Design artifacts generated and TSD published")
        return {
            **state,
            "design_artifacts":      artifacts,
            "api_contracts":         artifacts.get("api_design", state.get("api_contracts", [])),
            "confluence_tsd_page_id": tsd_page_id,
            "current_stage":         "design",
        }
