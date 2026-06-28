"""sdlc_orchestrator/agents/design_agent.py
Produces architecture diagrams, API design, and DB schema artifacts.
"""
import json
from langchain_core.messages import HumanMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.tools.confluence_tools import create_page

SYSTEM_PROMPT = """You are a Senior Solutions Architect.
Design the technical architecture for the stories below.
Return ONLY valid JSON:
{
  "architecture_diagram": "Mermaid flowchart string",
  "api_design": [{"endpoint": "...", "method": "GET|POST|PUT|DELETE", "request": {}, "response": {}, "auth": true}],
  "db_schema": [{"table": "...", "columns": [{"name": "...", "type": "...", "constraints": "..."}]}],
  "component_breakdown": [{"component": "...", "responsibility": "...", "tech": "..."}],
  "security_notes": ["..."],
  "performance_notes": ["..."]
}"""


async def design_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("design", state):
        emit(EventType.INFO, "DesignAgent starting — generating architecture artifacts")

        mem = SharedMemory(state["project_id"])
        await mem.init()

        ctx       = await mem.get_project_context()
        learnings = await mem.get_accumulated_learnings(limit=5)

        stories = state.get("stories", [])
        if not stories:
            raise ValueError("No stories in state — run story agent first")

        llm = ProviderFactory.get_model(AgentRole.DESIGN)

        stories_text = "\n".join(
            f"- [{s.get('jira_key', 'N/A')}] {s.get('summary', '')} | tags: {s.get('tags', [])}"
            for s in stories
        )

        adrs = json.dumps(state.get("architecture_decisions", []), indent=2)
        apis = json.dumps(state.get("api_contracts", []), indent=2)

        learning_ctx = ""
        if learnings:
            learning_ctx = "\nPrior learnings:\n" + json.dumps(
                [l["content"] for l in learnings[:3]], indent=2
            )

        prompt = (
            f"{SYSTEM_PROMPT}{learning_ctx}\n\n"
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
            f"Existing ADRs:\n{adrs}\n"
            f"Existing API contracts:\n{apis}\n\n"
            f"Stories:\n{stories_text}"
        )

        emit(EventType.LLM, "Calling LLM to design architecture")
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw      = response.content

        try:
            artifacts = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            artifacts = json.loads(m.group()) if m else {}

        # Publish design doc to Confluence
        try:
            diagram = artifacts.get("architecture_diagram", "")
            html_body = (
                f"<h2>Architecture Diagram</h2>"
                f"<ac:structured-macro ac:name='code'>"
                f"<ac:parameter ac:name='language'>mermaid</ac:parameter>"
                f"<ac:plain-text-body><![CDATA[{diagram}]]></ac:plain-text-body>"
                f"</ac:structured-macro>"
                f"<h2>API Design</h2><pre>{json.dumps(artifacts.get('api_design', []), indent=2)}</pre>"
                f"<h2>DB Schema</h2><pre>{json.dumps(artifacts.get('db_schema', []), indent=2)}</pre>"
            )
            page = await create_page(
                title=f"Design — {state.get('project_name', 'SDLC')}",
                body_html=html_body,
            )
            emit(EventType.TOOL, f"Published design doc to Confluence: {page.get('id')}")
        except Exception as e:
            emit(EventType.ERROR, f"Confluence publish failed: {e}")

        await mem.update_project_context({
            "api_contracts": artifacts.get("api_design", state.get("api_contracts", [])),
            "db_schema":     artifacts.get("db_schema", []),
        })

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "design",
            "learning_type":"design_artifacts",
            "content":      {
                "endpoints":   len(artifacts.get("api_design", [])),
                "tables":      len(artifacts.get("db_schema", [])),
                "components":  len(artifacts.get("component_breakdown", [])),
            },
            "metadata": {},
        })

        emit(EventType.DONE, "Design artifacts generated and published")

        return {
            **state,
            "design_artifacts": artifacts,
            "api_contracts":    artifacts.get("api_design", state.get("api_contracts", [])),
            "current_stage":    "design",
        }
