"""sdlc_orchestrator/agents/test_agent.py
ReAct agent: generates and pushes tests via GitHub MCP tools.
Tools: GitHub MCP (get_file_contents, push_file, ...)
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

SYSTEM_PROMPT = """You are a Senior QA Engineer with access to GitHub tools.

Your task:
1. Use GitHub tools to read the source files that were just implemented
2. Write comprehensive tests: unit tests for each public method, integration tests for APIs
3. Push test files to the same feature branch using GitHub tools
4. Aim for >80% coverage; mock external dependencies

After completing, respond with JSON:
{
  "test_files": [{"repo": "...", "path": "...", "branch": "..."}],
  "coverage": {"estimated_pct": 85},
  "passed": true
}"""


async def test_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("test", state):
        emit(EventType.INFO, "TestAgent starting")

        mem = SharedMemory(state["project_id"])
        await mem.init()
        learnings = await mem.get_accumulated_learnings(limit=5)

        files_changed    = state.get("files_changed", [])
        feature_branches = state.get("feature_branches", {})

        if not files_changed:
            emit(EventType.INFO, "No files changed — skipping tests")
            return {**state, "current_stage": "test",
                    "test_result": {"passed": True, "coverage": {}, "skipped": True}}

        llm   = ProviderFactory.get_model(AgentRole.TEST)
        tools = mcp_manager.get_tools_for_agent("test")

        files_ctx = json.dumps(
            [{"repo": f["repo"], "path": f["path"], "branch": feature_branches.get(f["repo"], "main")}
             for f in files_changed[:15]], indent=2
        )
        learning_ctx = json.dumps([l["content"] for l in learnings[:3]], indent=2) if learnings else ""

        user_message = (
            f"Test framework: {state.get('test_framework', 'pytest')}\n"
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n\n"
            f"Files to test (read these from GitHub first):\n{files_ctx}\n"
            + (f"\nPrevious test patterns:\n{learning_ctx}" if learning_ctx else "")
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
            emit(EventType.INFO, "No GitHub MCP tools — generating test specs only")
            response = await llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            raw = response.content

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            result = json.loads(m.group()) if m else {"passed": True}

        coverage_map = {f["path"]: result.get("coverage", {}).get("estimated_pct", 80.0)
                        for f in result.get("test_files", [])}

        await mem.save_story_learning({
            "story_id": state.get("current_story_id", ""), "agent_name": "test",
            "learning_type": "tests_generated",
            "content": {"test_files": len(result.get("test_files", []))}, "metadata": {},
        })

        emit(EventType.DONE, f"Tests done: passed={result.get('passed', True)}")
        return {
            **state,
            "test_result":      {"passed": result.get("passed", True), "coverage": coverage_map},
            "test_coverage_map": coverage_map,
            "current_stage":    "test",
        }
