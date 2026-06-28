"""sdlc_orchestrator/agents/story_agent.py
Converts requirements into Jira stories and assigns them to repos.
"""
import json
from langchain_core.messages import HumanMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.tools.jira_tools import create_story, EPIC_MAP

SYSTEM_PROMPT = """You are a Senior Product Manager.
Convert the requirements below into actionable Jira user stories.
Return ONLY valid JSON array:
[
  {
    "summary": "As a ... I want ... so that ...",
    "description": "...",
    "story_points": 3,
    "tags": ["backend", "api"],
    "acceptance_criteria": ["Given...", "When...", "Then..."],
    "epic": "platform_foundation"
  }
]
Epics: platform_foundation | agent_implementation | monitoring_dashboard | infrastructure"""


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
        emit(EventType.INFO, "StoryAgent starting — converting requirements to stories")

        mem = SharedMemory(state["project_id"])
        await mem.init()

        ctx       = await mem.get_project_context()
        learnings = await mem.get_accumulated_learnings(limit=5)

        requirements = state.get("requirements", [])
        if not requirements:
            raise ValueError("No requirements in state — run confluence agent first")

        llm = ProviderFactory.get_model(AgentRole.STORY)

        learning_ctx = ""
        if learnings:
            learning_ctx = "\nPrevious patterns:\n" + json.dumps(
                [l["content"] for l in learnings[:3]], indent=2
            )

        prompt = (
            f"{SYSTEM_PROMPT}{learning_ctx}\n\n"
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n\n"
            f"Requirements:\n" + "\n".join(f"- {r}" for r in requirements)
        )

        emit(EventType.LLM, "Calling LLM to generate stories")
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw      = response.content

        try:
            stories = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            stories = json.loads(m.group()) if m else []

        emit(EventType.INFO, f"Generated {len(stories)} stories — creating in Jira")

        assigned_repos = []
        for story in stories:
            epic_key = EPIC_MAP.get(story.get("epic", "agent_implementation"), "CTS-130")
            try:
                jira_issue = await create_story(
                    summary=story["summary"],
                    description=story.get("description", ""),
                    epic_key=epic_key,
                    story_points=story.get("story_points", 3),
                    labels=story.get("tags", []),
                )
                story["jira_key"] = jira_issue.get("key", "")
                emit(EventType.TOOL, f"Created Jira story {story['jira_key']}: {story['summary'][:60]}")
            except Exception as e:
                emit(EventType.ERROR, f"Jira create failed: {e}")
                story["jira_key"] = ""

            repos = resolve_repos(story)
            story["repos"] = repos
            assigned_repos.append({
                "story_id": story.get("jira_key", story["summary"][:20]),
                "repos":    repos,
                "tags":     story.get("tags", []),
            })

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "story",
            "learning_type":"stories_generated",
            "content":      {"story_count": len(stories), "epics_used": list({s.get("epic") for s in stories})},
            "metadata":     {},
        })

        emit(EventType.DONE, f"Created {len(stories)} stories across {len(assigned_repos)} repo assignments")

        return {
            **state,
            "stories":       stories,
            "assigned_repos": assigned_repos,
            "current_stage": "stories",
        }
