"""sdlc_orchestrator/agents/confluence_agent.py
Reads a Confluence idea page and extracts structured requirements.
"""
import json
from langchain_core.messages import HumanMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.tools.confluence_tools import get_page_content, extract_text_from_page

SYSTEM_PROMPT = """You are a Senior Business Analyst.
Extract structured requirements from the idea description below.
Return ONLY valid JSON with this structure:
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
        emit(EventType.INFO, "ConfluenceAgent starting — fetching idea from Confluence")

        mem = SharedMemory(state["project_id"])
        await mem.init()

        ctx       = await mem.get_project_context()
        learnings = await mem.get_accumulated_learnings(limit=5)

        idea_text = state.get("idea_raw", "")

        # Try to fetch from Confluence if a page URL is given
        page_url = state.get("confluence_page_url", "")
        if page_url and not idea_text:
            try:
                # Extract page ID from URL (last numeric segment)
                page_id = [s for s in page_url.rstrip("/").split("/") if s.isdigit()][-1]
                page    = await get_page_content(page_id)
                idea_text = await extract_text_from_page(page)
                emit(EventType.INFO, f"Fetched Confluence page {page_id} ({len(idea_text)} chars)")
            except Exception as e:
                emit(EventType.ERROR, f"Confluence fetch failed: {e}. Using raw idea.")

        if not idea_text:
            raise ValueError("No idea text provided and Confluence page fetch failed")

        llm = ProviderFactory.get_model(AgentRole.CONFLUENCE)

        learning_ctx = ""
        if learnings:
            learning_ctx = "\n\nPrevious learnings:\n" + json.dumps(learnings[:3], indent=2)

        prompt = f"{SYSTEM_PROMPT}{learning_ctx}\n\nIdea:\n{idea_text}"
        emit(EventType.LLM, "Calling LLM to extract requirements")

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw      = response.content

        emit(EventType.LLM, f"LLM returned {len(raw)} chars")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            parsed = json.loads(m.group()) if m else {}

        # Persist context to Redis
        await mem.update_project_context({
            "project_name":           parsed.get("project_name", state.get("project_name", "")),
            "tech_stack":             parsed.get("tech_stack", []),
            "requirements":           parsed.get("requirements", []),
            "architecture_decisions": parsed.get("architecture_decisions", []),
            "api_contracts":          parsed.get("api_contracts", []),
            "code_conventions":       parsed.get("code_conventions", {}),
        })

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", "init"),
            "agent_name":   "confluence",
            "learning_type":"requirements_extracted",
            "content":      {"requirement_count": len(parsed.get("requirements", []))},
            "metadata":     {},
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
