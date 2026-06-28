"""sdlc_orchestrator/agents/design_agent.py
ReAct agent: produces architecture artifacts and publishes to Confluence.
Tools: Atlassian MCP (create_confluence_page, update_confluence_page, ...)
"""
import json
import re
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.mcp.client import mcp_manager

SYSTEM_PROMPT = """You are a Senior Solutions Architect with access to Confluence tools.

Your task:
1. Design the technical architecture for the stories provided
2. Create a Confluence page in the SD space under parent page 50200578
   with the design doc (architecture diagram, API design, DB schema)
3. Respond with ONLY valid JSON:

{
  "architecture_diagram": "Mermaid flowchart string",
  "api_design": [{"endpoint": "...", "method": "GET|POST|PUT|DELETE", "request": {}, "response": {}, "auth": true}],
  "db_schema": [{"table": "...", "columns": [{"name": "...", "type": "...", "constraints": "..."}]}],
  "component_breakdown": [{"component": "...", "responsibility": "...", "tech": "..."}],
  "security_notes": ["..."],
  "performance_notes": ["..."],
  "confluence_page_id": "..."
}"""


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
            f"- [{s.get('jira_key','N/A')}] {s.get('summary','')} | tags: {s.get('tags',[])}"
            for s in stories
        )
        learning_ctx = json.dumps([l["content"] for l in learnings[:3]], indent=2) if learnings else ""

        user_message = (
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
            f"Existing ADRs:\n{json.dumps(state.get('architecture_decisions', []), indent=2)}\n\n"
            f"Stories:\n{stories_text}\n"
            + (f"\nPrevious learnings:\n{learning_ctx}" if learning_ctx else "")
        )

        # Phase 1: LLM generates design artifacts
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

        # Phase 2: publish TSD to Confluence and link it to the first Jira story
        tsd_page_id = artifacts.get("confluence_page_id", "")
        if tools:
            create_tool = next(
                (t for t in tools if "create" in t.name.lower() and "page" in t.name.lower()), None
            )
            if create_tool:
                api_rows = "".join(
                    f"<tr><td>{e.get('method','')}</td><td>{e.get('endpoint','')}</td><td>{e.get('description','')}</td></tr>"
                    for e in artifacts.get("api_design", [])[:10]
                )
                tsd_body = (
                    f"<h2>Architecture</h2><pre>{artifacts.get('architecture_diagram','')}</pre>"
                    f"<h2>API Design</h2><table><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr>{api_rows}</table>"
                    f"<h2>Security Notes</h2><ul>{''.join(f'<li>{n}</li>' for n in artifacts.get('security_notes',[]))}</ul>"
                )
                project_name = state.get("project_name", "Project")
                try:
                    result = await create_tool.ainvoke({
                        "space_key": "SD",
                        "parent_id": "50200578",
                        "title":     f"{project_name} — Technical Design",
                        "content":   tsd_body,
                    })
                    tsd_page_id = result.get("id", "") if isinstance(result, dict) else ""
                    emit(EventType.TOOL, f"Created TSD Confluence page id={tsd_page_id}")
                except Exception as e:
                    emit(EventType.ERROR, f"Confluence TSD creation failed: {e}")

            # Link TSD page to first Jira story
            if tsd_page_id and state.get("stories"):
                link_tool = next(
                    (t for t in tools if "remote" in t.name.lower() or "link" in t.name.lower()), None
                )
                comment_tool = next(
                    (t for t in tools if "comment" in t.name.lower() and "jira" in t.name.lower()), None
                )
                first_story_key = state["stories"][0].get("jira_key", "")
                if first_story_key and comment_tool:
                    try:
                        await comment_tool.ainvoke({
                            "issue_key": first_story_key,
                            "body":      f"Technical Design Document created: [View TSD|https://bhaskarmca83.atlassian.net/wiki/spaces/SD/pages/{tsd_page_id}] — please review and approve via the pipeline gate.",
                        })
                        emit(EventType.TOOL, f"Linked TSD to Jira story {first_story_key}")
                    except Exception as e:
                        emit(EventType.ERROR, f"Jira comment failed: {e}")

        artifacts["confluence_page_id"] = tsd_page_id

        await mem.update_project_context({
            "api_contracts": artifacts.get("api_design", state.get("api_contracts", [])),
            "db_schema":     artifacts.get("db_schema", []),
        })
        await mem.save_story_learning({
            "story_id": state.get("current_story_id", ""), "agent_name": "design",
            "learning_type": "design_artifacts",
            "content": {"endpoints": len(artifacts.get("api_design", []))}, "metadata": {},
        })

        emit(EventType.DONE, "Design artifacts generated")
        return {
            **state,
            "design_artifacts": artifacts,
            "api_contracts":    artifacts.get("api_design", state.get("api_contracts", [])),
            "current_stage":    "design",
        }
