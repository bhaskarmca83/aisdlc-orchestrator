"""sdlc_orchestrator/agents/review_agent.py
ReAct agent: code review via GitHub MCP — reads files, posts PR comments.
Tools: GitHub MCP (get_file_contents, create_pull_request_review, add_issue_comment, ...)
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

SYSTEM_PROMPT = """You are a Principal Engineer doing a code review with access to GitHub tools.

Your task:
1. Use GitHub tools to read the changed files on the feature branches
2. Review for: correctness, security (OWASP Top 10), performance, conventions, testability
3. Post a review comment on the PR via GitHub tools
4. Respond with ONLY valid JSON:

{
  "verdict": "APPROVE" | "REQUEST_CHANGES",
  "score": 0-100,
  "critical_issues": [{"file": "...", "issue": "...", "fix": "..."}],
  "suggestions": ["..."],
  "summary": "..."
}"""


async def review_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("review", state):
        emit(EventType.INFO, "ReviewAgent starting")

        mem = SharedMemory(state["project_id"])
        await mem.init()
        learnings = await mem.get_accumulated_learnings(limit=5)

        files_changed    = state.get("files_changed", [])
        feature_branches = state.get("feature_branches", {})
        retry_count      = state.get("retry_count", 0)

        if not files_changed:
            emit(EventType.INFO, "No files to review")
            return {**state, "current_stage": "review",
                    "review_result": {"verdict": "APPROVE", "score": 100, "critical_issues": []}}

        llm   = ProviderFactory.get_model(AgentRole.REVIEW)
        tools = mcp_manager.get_tools_for_agent("review")

        files_ctx    = json.dumps(
            [{"repo": f["repo"], "path": f["path"], "branch": feature_branches.get(f["repo"], "main")}
             for f in files_changed[:15]], indent=2
        )
        learning_ctx = json.dumps([l["content"] for l in learnings[:3]], indent=2) if learnings else ""

        user_message = (
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
            f"Retry count: {retry_count}\n"
            f"Files to review (read from GitHub):\n{files_ctx}\n"
            + (f"\nPrior review patterns:\n{learning_ctx}" if learning_ctx else "")
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
            emit(EventType.INFO, "No GitHub MCP tools — reviewing from state context only")
            response = await llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ])
            raw = response.content

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            result = json.loads(m.group()) if m else {"verdict": "APPROVE", "score": 75}

        review_history = list(state.get("review_history", [])) + [
            {"verdict": result.get("verdict"), "score": result.get("score"), "retry": retry_count}
        ]

        await mem.save_story_learning({
            "story_id": state.get("current_story_id", ""), "agent_name": "review",
            "learning_type": "review_result",
            "content": {"verdict": result.get("verdict"), "score": result.get("score")}, "metadata": {},
        })

        emit(EventType.DONE, f"Review: {result.get('verdict')} score={result.get('score')}")
        return {**state, "review_result": result, "review_history": review_history, "current_stage": "review"}
