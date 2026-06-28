"""sdlc_orchestrator/graph.py
LangGraph topology — corrected SDLC flow:
  confluence → stories → [PO Gate] → design → [Arch Gate]
  → implement → test → review → deploy → e2e
Two interrupt_before gates give human sign-off at the right checkpoints.
"""
import os
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.agents.confluence_agent     import confluence_agent_node
from sdlc_orchestrator.agents.story_agent          import story_agent_node
from sdlc_orchestrator.agents.design_agent         import design_agent_node
from sdlc_orchestrator.agents.implementation_agent import implementation_agent_node
from sdlc_orchestrator.agents.test_agent           import test_agent_node
from sdlc_orchestrator.agents.review_agent         import review_agent_node
from sdlc_orchestrator.agents.deploy_agent         import deploy_agent_node
from sdlc_orchestrator.agents.e2e_test_agent       import e2e_test_agent_node
from sdlc_orchestrator.monitoring.tracker          import EventType, emit

MAX_RETRY = int(os.environ.get("AGENT_MAX_RETRY", "2"))


# ─── Gate nodes ───────────────────────────────────────────────────────────────

async def po_gate_node(state: SDLCState) -> SDLCState:
    """Gate 1: Product Owner reviews and approves stories before design starts."""
    emit(EventType.GATE, "PO approval received — proceeding to technical design")
    approval = state.get("po_approval", {})
    if approval and not approval.get("approved", True):
        raise ValueError(f"PO rejected stories: {approval.get('reason', 'no reason given')}")
    return {**state, "current_stage": "po_gate"}


async def arch_gate_node(state: SDLCState) -> SDLCState:
    """Gate 2: Architect reviews TSD and approves before implementation starts."""
    emit(EventType.GATE, "Architect approval received — proceeding to implementation")
    approval = state.get("arch_approval", {})
    if approval and not approval.get("approved", True):
        raise ValueError(f"Architect rejected TSD: {approval.get('reason', 'no reason given')}")
    return {**state, "current_stage": "arch_gate"}


# ─── Conditional routing ──────────────────────────────────────────────────────

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
    # Proceed to E2E if deployment happened at all (success OR planned for local dev)
    if deploy_status:
        return "e2e"
    emit(EventType.ROUTE, "No deploy info — skipping E2E")
    return END


# ─── Retry wrapper ────────────────────────────────────────────────────────────

async def implement_with_retry(state: SDLCState) -> SDLCState:
    new_state = await implementation_agent_node(state)
    return {**new_state, "retry_count": state.get("retry_count", 0) + 1}


# ─── Graph construction ───────────────────────────────────────────────────────

checkpointer = MemorySaver()


def build_graph():
    builder = StateGraph(SDLCState)

    builder.add_node("confluence", confluence_agent_node)
    builder.add_node("stories",    story_agent_node)
    builder.add_node("po_gate",    po_gate_node)       # Gate 1: PO approves stories
    builder.add_node("design",     design_agent_node)
    builder.add_node("arch_gate",  arch_gate_node)     # Gate 2: Architect approves TSD
    builder.add_node("implement",  implement_with_retry)
    builder.add_node("test",       test_agent_node)
    builder.add_node("review",     review_agent_node)
    builder.add_node("deploy",     deploy_agent_node)
    builder.add_node("e2e",        e2e_test_agent_node)

    builder.set_entry_point("confluence")
    builder.add_edge("confluence", "stories")
    builder.add_edge("stories",    "po_gate")    # pause — PO reviews stories
    builder.add_edge("po_gate",    "design")
    builder.add_edge("design",     "arch_gate")  # pause — Architect reviews TSD
    builder.add_edge("arch_gate",  "implement")
    builder.add_edge("implement",  "test")

    builder.add_conditional_edges("test",   route_after_test,   {"review": "review",  "implement": "implement"})
    builder.add_conditional_edges("review", route_after_review, {"deploy": "deploy",  "implement": "implement"})
    builder.add_conditional_edges("deploy", route_after_deploy, {"e2e": "e2e",        END: END})
    builder.add_edge("e2e", END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["po_gate", "arch_gate"],  # pause at both gates
    )


graph = build_graph()
