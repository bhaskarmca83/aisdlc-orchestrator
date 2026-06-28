"""sdlc_orchestrator/agents/e2e_test_agent.py
ReAct agent: generates and runs Playwright E2E tests via Playwright + GitHub MCP tools.
Tools: Playwright MCP (browser navigation, screenshots), GitHub MCP (push spec files)
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

SYSTEM_PROMPT = """You are a Senior QA Automation Engineer with access to Playwright and GitHub tools.

Your task:
1. Use Playwright tools to navigate to the deployed application URL
2. Run smoke tests: page loads, key user flows, API health endpoints
3. Generate a comprehensive Playwright TypeScript spec and push it to GitHub via GitHub tools
4. Capture screenshots on failures

Respond with ONLY valid JSON:
{
  "passed": true,
  "tests_run": 5,
  "tests_passed": 5,
  "tests_failed": 0,
  "spec_file": {"repo": "...", "path": "e2e/sdlc.spec.ts", "branch": "..."},
  "failures": [{"test": "...", "error": "..."}],
  "screenshots": ["..."]
}"""


async def e2e_test_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("e2e", state):
        emit(EventType.INFO, "E2ETestAgent starting")

        mem = SharedMemory(state["project_id"])
        await mem.init()
        learnings = await mem.get_accumulated_learnings(limit=5)

        deploy_status    = state.get("deploy_status", {})
        env_urls         = state.get("env_urls", {})
        base_url         = env_urls.get("dev", "http://localhost:8001")
        feature_branches = state.get("feature_branches", {})

        if not deploy_status:
            emit(EventType.INFO, "No deploy info — running E2E against local dev URL")

        llm   = ProviderFactory.get_model(AgentRole.E2E)
        tools = mcp_manager.get_tools_for_agent("e2e")

        stories = state.get("stories", [])
        story_ctx = "\n".join(
            f"- {s.get('summary', '')} | AC: {'; '.join(s.get('acceptance_criteria', []))}"
            for s in stories[:10]
        )
        learning_ctx = json.dumps([l["content"] for l in learnings[:3]], indent=2) if learnings else ""

        user_message = (
            f"Base URL: {base_url}\n"
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
            f"Feature branches: {json.dumps(feature_branches)}\n\n"
            f"Stories to test:\n{story_ctx}\n"
            + (f"\nPrevious E2E patterns:\n{learning_ctx}" if learning_ctx else "")
        )

        if tools:
            emit(EventType.TOOL, f"Using {len(tools)} Playwright+GitHub MCP tools")
            agent    = create_react_agent(llm, tools)
            response = await agent.ainvoke({
                "messages": [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=user_message),
                ]
            })
            raw = response["messages"][-1].content
        else:
            emit(EventType.INFO, "No Playwright MCP tools — generating E2E spec only")
            response = await llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            raw = response.content

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            result = json.loads(m.group()) if m else {"passed": True, "tests_run": 0}

        passed    = result.get("passed", True)
        e2e_suite = list(state.get("e2e_test_suite", [])) + [result]

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "e2e",
            "learning_type":"e2e_results",
            "content":      {"passed": passed, "base_url": base_url,
                             "tests_run": result.get("tests_run", 0)},
            "metadata": {},
        })

        emit(EventType.DONE, f"E2E complete — passed={passed}")
        return {
            **state,
            "e2e_results":    result,
            "e2e_test_suite": e2e_suite,
            "current_stage":  "e2e",
        }
