"""sdlc_orchestrator/graph.py
LangGraph topology: 9 nodes, linear flow with conditional retry loops and human gate.
"""
import os
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.redis import RedisSaver

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.agents.confluence_agent   import confluence_agent_node
from sdlc_orchestrator.agents.story_agent        import story_agent_node
from sdlc_orchestrator.agents.design_agent       import design_agent_node
from sdlc_orchestrator.agents.implementation_agent import implementation_agent_node
from sdlc_orchestrator.agents.test_agent         import test_agent_node
from sdlc_orchestrator.agents.review_agent       import review_agent_node
from sdlc_orchestrator.agents.deploy_agent       import deploy_agent_node
from sdlc_orchestrator.agents.e2e_test_agent     import e2e_test_agent_node
from sdlc_orchestrator.monitoring.tracker        import EventType, emit

MAX_RETRY = int(os.environ.get("AGENT_MAX_RETRY", "2"))


# ─── Human-in-the-loop gate node ──────────────────────────────────────────────

async def approval_gate_node(state: SDLCState) -> SDLCState:
    """Pause point — LangGraph interrupt_before means this node runs after resumption."""
    emit(EventType.GATE, "Human approval received — proceeding to implementation")
    approval = state.get("approval_payload", {})
    if approval and approval.get("rejected"):
        raise ValueError(f"Human rejected pipeline: {approval.get('reason', 'no reason given')}")
    return {**state, "current_stage": "gate"}


# ─── Conditional routing functions ────────────────────────────────────────────

def route_after_test(state: SDLCState) -> str:
    test_result = state.get("test_result", {})
    retry_count = state.get("retry_count", 0)
    if test_result.get("passed", False) or retry_count >= MAX_RETRY:
        return "review"
    emit(EventType.ROUTE, f"Tests failed (retry {retry_count + 1}/{MAX_RETRY}) — re-implementing")
    return "implement"


def route_after_review(state: SDLCState) -> str:
    review_result = state.get("review_result", {})
    retry_count   = state.get("retry_count", 0)
    verdict       = review_result.get("verdict", "APPROVE")
    if verdict == "APPROVE" or retry_count >= MAX_RETRY:
        return "deploy"
    emit(EventType.ROUTE, f"Review requested changes (retry {retry_count + 1}/{MAX_RETRY}) — re-implementing")
    return "implement"


def route_after_deploy(state: SDLCState) -> str:
    deploy_status = state.get("deploy_status", {})
    if any(v == "success" for v in deploy_status.values()):
        return "e2e"
    emit(EventType.ROUTE, "Deploy did not succeed — ending pipeline")
    return END


# ─── Retry wrapper ────────────────────────────────────────────────────────────

async def implement_with_retry(state: SDLCState) -> SDLCState:
    new_state = await implementation_agent_node(state)
    return {**new_state, "retry_count": state.get("retry_count", 0) + 1}


# ─── Graph construction ───────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    builder = StateGraph(SDLCState)

    builder.add_node("confluence", confluence_agent_node)
    builder.add_node("stories",    story_agent_node)
    builder.add_node("design",     design_agent_node)
    builder.add_node("gate",       approval_gate_node)
    builder.add_node("implement",  implement_with_retry)
    builder.add_node("test",       test_agent_node)
    builder.add_node("review",     review_agent_node)
    builder.add_node("deploy",     deploy_agent_node)
    builder.add_node("e2e",        e2e_test_agent_node)

    builder.set_entry_point("confluence")
    builder.add_edge("confluence", "stories")
    builder.add_edge("stories",    "design")
    builder.add_edge("design",     "gate")
    builder.add_edge("gate",       "implement")
    builder.add_edge("implement",  "test")

    builder.add_conditional_edges("test",   route_after_test,   {"review": "review",  "implement": "implement"})
    builder.add_conditional_edges("review", route_after_review, {"deploy": "deploy",  "implement": "implement"})
    builder.add_conditional_edges("deploy", route_after_deploy, {"e2e": "e2e",        END: END})
    builder.add_edge("e2e", END)

    checkpointer = RedisSaver.from_conn_string(os.environ["REDIS_URL"])

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["gate"],
    )


# Singleton graph instance
graph = build_graph()
