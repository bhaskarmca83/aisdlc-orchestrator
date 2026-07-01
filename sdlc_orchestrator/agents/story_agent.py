"""sdlc_orchestrator/agents/story_agent.py
ReAct agent: converts requirements into INVEST-compliant Jira stories with ACs and test cases.
Tools: Atlassian MCP (create_jira_issue, search_jira_issues, ...)
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
from sdlc_orchestrator.agents._utils import resolve_repos, find_mcp_tool, parse_mcp_key

SYSTEM_PROMPT = """You are a Senior Product Manager. Convert requirements into INVEST-compliant Jira user stories.

Output a JSON array ONLY — no prose, no markdown fences, just the raw JSON array:

[
  {
    "jira_key": "PROJ-TBD",
    "summary": "As a <role> I want <feature> so that <benefit>",
    "description": "Detailed description including context and business value.",
    "story_points": 3,
    "priority": "Medium",
    "tags": ["backend", "api"],
    "acceptance_criteria": [
      "Given <context>, When <action>, Then <outcome>",
      "Given <context>, When <action>, Then <outcome>"
    ],
    "repos": ["aisdlc-backend"]
  }
]

Rules:
- Generate 2–5 stories maximum; keep them focused and concrete.
- story_points must be one of: 1, 2, 3, 5, 8
- priority must be one of: Highest, High, Medium, Low, Lowest
- Each story must have at least 2 acceptance_criteria in Given/When/Then format
- tags ["api","backend"] or AC mentions "database" → repos: ["aisdlc-backend"]
- tags ["ui","frontend"] or AC mentions "screen","page","form" → repos: ["aisdlc-frontend"]
- tags ["infra","terraform"] or AC mentions "deploy" → repos: ["aisdlc-infra"]
- default → repos: ["aisdlc-backend"]"""


def _build_jira_description(story: dict) -> str:
    """Format description with ACs in Jira wiki markup."""
    desc = story.get("description", "")
    acs  = story.get("acceptance_criteria", [])
    if not acs:
        return desc
    ac_lines = "\n".join(f"* {ac}" for ac in acs)
    return f"{desc}\n\nh3. Acceptance Criteria\n{ac_lines}"


def _generate_test_cases(stories: list[dict]) -> list[dict]:
    """Derive one test case per AC at story creation — same cases used by test_agent + E2E."""
    test_cases = []
    for story in stories:
        story_key = story.get("jira_key", "STORY")
        for i, ac in enumerate(story.get("acceptance_criteria", []), 1):
            test_cases.append({
                "id":            f"{story_key}-TC{i:02d}",
                "story_id":      story_key,
                "story_summary": story.get("summary", ""),
                "scenario":      ac,
                "expected":      "Pass",
                "status":        "pending",
            })
    return test_cases


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

        # Inject PO revision reason if stories were previously rejected
        revision_reason = state.get("po_revision_reason", "")
        if revision_reason:
            user_message = (
                f"PREVIOUS STORIES REJECTED by Product Owner: {revision_reason}\n"
                f"Please revise the stories to address these concerns.\n\n"
            ) + user_message

        # Phase 1: LLM generates stories (no tools — small models can't handle 73 tools)
        emit(EventType.INFO, "Generating stories with LLM")
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

        # Resolve the target Jira project for THIS run
        jira_project = state.get("target_jira_project", "").strip() or os.environ.get("JIRA_PROJECT_KEY", "")
        if not jira_project:
            emit(EventType.ERROR,
                 "No Jira project key provided. Set jira_project_key in the run request. "
                 "Stories will be generated in-memory only.")

        # Phase 2: Create stories in Jira via targeted tool calls
        jira_synced = 0
        jira_failed = 0
        if tools and stories and jira_project:
            create_tool = next(
                (t for t in tools if "create" in t.name.lower() and "issue" in t.name.lower()), None
            )
            if create_tool:
                emit(EventType.TOOL, f"Creating {len(stories)} Jira issues in {jira_project} via MCP")
                for story in stories:
                    try:
                        result = await create_tool.ainvoke({
                            "project_key":  jira_project,
                            "summary":      story.get("summary", ""),
                            "description":  _build_jira_description(story),
                            "issue_type":   "Story",
                            "priority":     story.get("priority", "Medium"),
                            "labels":       story.get("tags", []),
                            "story_points": story.get("story_points", 3),
                        })
                        jira_key = parse_mcp_key(result)

                        if jira_key:
                            story["jira_key"] = jira_key
                            jira_synced += 1
                        else:
                            jira_failed += 1
                            emit(EventType.ERROR, f"Jira returned no key for '{story.get('summary','')[:40]}'")
                    except Exception as e:
                        jira_failed += 1
                        emit(EventType.ERROR, f"Jira create failed: {e}")
            else:
                emit(EventType.INFO, "No Jira create-issue tool found — stories generated in-memory only")
        else:
            emit(EventType.INFO, "No MCP tools available — stories generated in-memory only")

        for story in stories:
            if not story.get("repos"):
                story["repos"] = resolve_repos(story)

        assigned_repos = [
            {"story_id": s.get("jira_key", s["summary"][:20]), "repos": s["repos"], "tags": s.get("tags", [])}
            for s in stories
        ]

        if jira_failed > 0 and jira_synced == 0:
            emit(EventType.ERROR,
                 f"Jira sync FAILED for all {jira_failed} stories — check JIRA_BASE_URL and project key.")
        elif jira_failed > 0:
            emit(EventType.INFO, f"{jira_synced} stories synced to Jira, {jira_failed} failed.")
        else:
            emit(EventType.DONE, f"{len(stories)} stories created in Jira project {jira_project}")

        # Generate test cases from ACs at story creation (passed to test_agent + E2E)
        test_cases = _generate_test_cases(stories)
        emit(EventType.INFO, f"Generated {len(test_cases)} test cases from story ACs")

        await mem.save_story_learning({
            "story_id": state.get("current_story_id", ""), "agent_name": "story",
            "learning_type": "stories_generated",
            "content": {"story_count": len(stories), "test_case_count": len(test_cases)}, "metadata": {},
        })

        return {
            **state,
            "stories":        stories,
            "assigned_repos": assigned_repos,
            "test_cases":     test_cases,
            "current_stage":  "stories",
        }
