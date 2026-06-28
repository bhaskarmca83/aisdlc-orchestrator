"""sdlc_orchestrator/agents/story_agent.py
ReAct agent: converts requirements into Jira stories and assigns repos.
Tools: Atlassian MCP (create_jira_issue, search_jira_issues, ...)
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

SYSTEM_PROMPT = """You are a Senior Product Manager with access to Jira tools.

Your task:
1. Convert the requirements into actionable Jira user stories
2. Create each story in Jira under the appropriate epic using your Jira tools
   - Epics: CTS-129 (Platform Foundation), CTS-130 (Agent Implementation),
            CTS-131 (Monitoring Dashboard), CTS-132 (Infrastructure)
   - Project key: CTS
3. After creating stories in Jira, respond with a JSON array of stories created:

[
  {
    "jira_key": "CTS-XXX",
    "summary": "As a ... I want ... so that ...",
    "description": "...",
    "story_points": 3,
    "tags": ["backend", "api"],
    "acceptance_criteria": ["Given...", "When...", "Then..."],
    "epic": "CTS-130",
    "repos": ["aisdlc-backend"]
  }
]

Tag rules for repos:
- tags ["api","backend"] or acceptance criteria mentions "database" → aisdlc-backend
- tags ["ui","frontend"] or AC mentions "screen","page","form" → aisdlc-frontend
- tags ["infra","terraform"] or AC mentions "deploy" → aisdlc-infra
- default → aisdlc-backend"""


def resolve_repos(story: dict) -> list[str]:
    tags = story.get("tags", [])
    ac   = " ".join(story.get("acceptance_criteria", [])).lower()
    repos = []
    if any(t in tags for t in ["api", "backend"]) or "database" in ac:
        repos.append("aisdlc-backend")
    if any(t in tags for t in ["ui", "frontend"]) or any(w in ac for w in ["screen", "page", "form"]):
        repos.append("aisdlc-frontend")
    if any(t in tags for t in ["infra", "terraform"]) or "deploy" in ac:
        repos.append("aisdlc-infra")
    return repos or ["aisdlc-backend"]


async def story_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("stories", state):
        emit(EventType.INFO, "StoryAgent starting")

        mem = SharedMemory(state["project_id"])
        await mem.init()
        learnings = await mem.get_accumulated_learnings(limit=5)

        llm   = ProviderFactory.get_model(AgentRole.STORY)
        tools = mcp_manager.get_tools_for_agent("stories")

        requirements = state.get("requirements", [])
        if not requirements:
            raise ValueError("No requirements — run confluence agent first")

        req_text     = "\n".join(f"- {r}" for r in requirements)
        learning_ctx = json.dumps([l["content"] for l in learnings[:3]], indent=2) if learnings else ""

        user_message = (
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n\n"
            f"Requirements:\n{req_text}\n"
            + (f"\nPrevious patterns:\n{learning_ctx}" if learning_ctx else "")
        )

        if tools:
            emit(EventType.TOOL, f"Using {len(tools)} Atlassian MCP tools (will create Jira stories)")
            agent    = create_react_agent(llm, tools)
            response = await agent.ainvoke({
                "messages": [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_message),
                ]
            })
            raw = response["messages"][-1].content
        else:
            emit(EventType.INFO, "No Atlassian MCP tools — generating stories without Jira")
            response = await llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            raw = response.content

        try:
            stories = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            stories = json.loads(m.group()) if m else []

        for story in stories:
            if not story.get("repos"):
                story["repos"] = resolve_repos(story)

        assigned_repos = [
            {"story_id": s.get("jira_key", s["summary"][:20]), "repos": s["repos"], "tags": s.get("tags", [])}
            for s in stories
        ]

        emit(EventType.DONE, f"Created {len(stories)} stories")
        await mem.save_story_learning({
            "story_id": state.get("current_story_id", ""), "agent_name": "story",
            "learning_type": "stories_generated",
            "content": {"story_count": len(stories)}, "metadata": {},
        })

        return {**state, "stories": stories, "assigned_repos": assigned_repos, "current_stage": "stories"}
