"""sdlc_orchestrator/agents/implementation_agent.py
ReAct agent: generates code and pushes it to GitHub via MCP tools.
Tools: GitHub MCP (create_branch, push_file, create_pull_request, ...)
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
from sdlc_orchestrator.agents._utils import resolve_repos

SYSTEM_PROMPT = """You are a Senior Full-Stack Engineer with access to GitHub tools.

Your task for EACH story:
1. Create a feature branch named feature/{jira-key}-implementation in the appropriate repo
2. Generate complete, production-ready code covering all acceptance criteria
3. Push each file to the branch using your GitHub tools
4. Open a pull request

GitHub owner: bhaskarmca83
Repos: aisdlc-backend (Spring Boot/Java), aisdlc-frontend (React), aisdlc-infra (Terraform)

After completing all stories, respond with JSON:
{
  "files_changed": [{"repo": "...", "path": "...", "branch": "..."}],
  "feature_branches": {"repo-name": "branch-name"},
  "pull_requests": [{"repo": "...", "number": 0, "url": "..."}]
}"""


async def implementation_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("implement", state):
        emit(EventType.INFO, "ImplementationAgent starting")

        mem = SharedMemory(state["project_id"])
        await mem.init()
        learnings = await mem.get_accumulated_learnings(limit=5)

        llm   = ProviderFactory.get_model(AgentRole.IMPLEMENTATION)
        tools = mcp_manager.get_tools_for_agent("implement")

        stories = state.get("stories", [])
        if not stories:
            raise ValueError("No stories for implementation")

        conventions      = json.dumps(state.get("code_conventions", {}), indent=2)
        api_ctx          = json.dumps(state.get("api_contracts", [])[:5], indent=2)
        design_artifacts = state.get("design_artifacts", {})
        db_schema_ctx    = json.dumps(design_artifacts.get("db_schema", [])[:5], indent=2)
        component_ctx    = json.dumps(design_artifacts.get("component_breakdown", [])[:5], indent=2)
        learning_ctx     = json.dumps([l["content"] for l in learnings[:3]], indent=2) if learnings else ""

        stories_text = "\n\n".join(
            f"Story: [{s.get('jira_key','N/A')}] {s.get('summary','')}\n"
            f"Description: {s.get('description','')}\n"
            f"AC: {'; '.join(s.get('acceptance_criteria',[]))}\n"
            f"Repos: {', '.join(resolve_repos(s))}"
            for s in stories
        )

        user_message = (
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
            f"Code conventions:\n{conventions}\n"
            f"API contracts:\n{api_ctx}\n"
            f"DB schema:\n{db_schema_ctx}\n"
            f"Component breakdown:\n{component_ctx}\n\n"
            f"Stories to implement:\n{stories_text}\n"
            + (f"\nPrevious patterns:\n{learning_ctx}" if learning_ctx else "")
        )

        if tools:
            emit(EventType.TOOL, f"Using {len(tools)} GitHub MCP tools")
            agent    = create_react_agent(llm, tools)
            response = await agent.ainvoke({
                "messages": [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_message),
                ]
            })
            raw = response["messages"][-1].content
        else:
            emit(EventType.INFO, "No GitHub MCP tools — generating code only (no push)")
            response = await llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            raw = response.content

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            result = json.loads(m.group()) if m else {}

        files_changed    = result.get("files_changed", [])
        feature_branches = result.get("feature_branches", {})

        await mem.save_story_learning({
            "story_id": state.get("current_story_id", ""), "agent_name": "implementation",
            "learning_type": "files_generated",
            "content": {"file_count": len(files_changed)}, "metadata": {},
        })

        emit(EventType.DONE, f"Implementation complete: {len(files_changed)} file(s)")
        return {
            **state,
            "files_changed":    files_changed,
            "feature_branches": feature_branches,
            "current_stage":    "implement",
            # retry_count is incremented in implement_with_retry wrapper — not here
        }
