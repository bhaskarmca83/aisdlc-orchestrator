"""sdlc_orchestrator/graph.py
LangGraph topology — intake-classified SDLC flow with revision loops and two-tier E2E.

Intake classifies the input and routes to the right entry stage:
  fresh_idea      → confluence → stories → [PO Gate] → design → [Arch Gate] → ...
  existing_story  → design → [Arch Gate] → implement → ...
  spike           → design → [Arch Gate] → implement → ...
  defect          → implement → test → review → ...

All paths converge at: implement → test → review → deploy_local → e2e_local → deploy_cloud → e2e_cloud

Gates default to REJECT when payload is absent.
PO rejection loops back to stories; arch rejection loops back to design.
Local E2E blocks cloud deploy if it fails (unless it was skipped for a valid reason).
"""
import os
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.agents.intake_agent           import intake_agent_node
from sdlc_orchestrator.agents.confluence_agent      import confluence_agent_node
from sdlc_orchestrator.agents.story_agent           import story_agent_node
from sdlc_orchestrator.agents.design_agent          import design_agent_node
from sdlc_orchestrator.agents.implementation_agent  import implementation_agent_node
from sdlc_orchestrator.agents.test_agent            import test_agent_node
from sdlc_orchestrator.agents.review_agent          import review_agent_node
from sdlc_orchestrator.agents.deploy_local_agent    import deploy_local_agent_node
from sdlc_orchestrator.agents.e2e_local_agent       import e2e_local_agent_node
from sdlc_orchestrator.agents.deploy_cloud_agent    import deploy_cloud_agent_node
from sdlc_orchestrator.agents.e2e_cloud_agent       import e2e_cloud_agent_node
from sdlc_orchestrator.monitoring.tracker           import EventType, emit

MAX_RETRY = int(os.environ.get("AGENT_MAX_RETRY", "2"))


# ─── Intake routing ───────────────────────────────────────────────────────────

def route_after_intake(state: SDLCState) -> str:
    entry_type = state.get("entry_type", "fresh_idea")
    routes = {
        "fresh_idea":      "confluence",
        "existing_story":  "design",
        "spike":           "design",
        "defect":          "implement",
    }
    dest = routes.get(entry_type, "confluence")
    emit(EventType.ROUTE, f"Entry type '{entry_type}' → starting at '{dest}'")
    return dest


# ─── Gate nodes ───────────────────────────────────────────────────────────────

async def po_gate_node(state: SDLCState) -> SDLCState:
    """Gate 1: PO reviews stories. Rejected → loop back to story agent with reason."""
    approval = state.get("po_approval")
    if not approval or not approval.get("approved", False):
        reason = (approval or {}).get("reason", "no reason given")
        emit(EventType.GATE, f"PO rejected stories: {reason} — requesting revision")
        return {**state, "current_stage": "po_gate", "po_revision_reason": reason, "po_approval": None}
    emit(EventType.GATE, "PO approved stories — proceeding to technical design")
    return {**state, "current_stage": "po_gate", "po_revision_reason": None}


async def arch_gate_node(state: SDLCState) -> SDLCState:
    """Gate 2: Architect reviews TSD. Rejected → loop back to design agent with reason."""
    approval = state.get("arch_approval")
    if not approval or not approval.get("approved", False):
        reason = (approval or {}).get("reason", "no reason given")
        emit(EventType.GATE, f"Architect rejected TSD: {reason} — requesting revision")
        return {**state, "current_stage": "arch_gate", "arch_revision_reason": reason, "arch_approval": None}
    emit(EventType.GATE, "Architect approved TSD — proceeding to implementation")
    return {**state, "current_stage": "arch_gate", "arch_revision_reason": None}


# ─── Conditional routing ──────────────────────────────────────────────────────

def route_after_po_gate(state: SDLCState) -> str:
    if state.get("po_revision_reason"):
        emit(EventType.ROUTE, "PO rejected — routing back to story agent for revision")
        return "stories"
    return "design"


def route_after_arch_gate(state: SDLCState) -> str:
    if state.get("arch_revision_reason"):
        emit(EventType.ROUTE, "Architect rejected — routing back to design agent for revision")
        return "design"
    return "implement"


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
        return "deploy_local"
    emit(EventType.ROUTE, f"Review requested changes (retry {retry_count + 1}/{MAX_RETRY}) — re-implementing")
    return "implement"


def route_after_e2e_local(state: SDLCState) -> str:
    stage_statuses = state.get("stage_statuses", {})
    e2e_status     = stage_statuses.get("e2e_local", "")
    e2e_results    = state.get("e2e_local_results", {})

    if e2e_status == "skipped":
        emit(EventType.ROUTE, "Local E2E skipped — proceeding to cloud deploy")
        return "deploy_cloud"
    if e2e_results.get("passed", False):
        emit(EventType.ROUTE, "Local E2E passed — proceeding to cloud deploy")
        return "deploy_cloud"
    emit(EventType.ROUTE, "Local E2E failed — stopping before cloud deploy")
    return END


def route_after_deploy_cloud(state: SDLCState) -> str:
    deployment_url = state.get("deployment_url", "")
    stage_statuses = state.get("stage_statuses", {})
    cloud_status   = stage_statuses.get("deploy_cloud", "")

    if cloud_status == "skipped":
        emit(EventType.ROUTE, "Cloud deploy skipped — skipping cloud E2E")
        return END
    if deployment_url:
        emit(EventType.ROUTE, f"Cloud deploy ready at {deployment_url} — running cloud E2E")
        return "e2e_cloud"
    emit(EventType.ROUTE, "No deployment URL — skipping cloud E2E")
    return END


# ─── Retry wrapper ────────────────────────────────────────────────────────────

async def implement_with_retry(state: SDLCState) -> SDLCState:
    """Wraps implementation_agent_node; retry_count is incremented HERE only."""
    new_state = await implementation_agent_node(state)
    return {**new_state, "retry_count": state.get("retry_count", 0) + 1}


# ─── Graph construction ───────────────────────────────────────────────────────

checkpointer = MemorySaver()


def build_graph():
    builder = StateGraph(SDLCState)

    # ── Nodes ──────────────────────────────────────────────────────────────────
    builder.add_node("intake",        intake_agent_node)
    builder.add_node("confluence",    confluence_agent_node)
    builder.add_node("stories",       story_agent_node)
    builder.add_node("po_gate",       po_gate_node)
    builder.add_node("design",        design_agent_node)
    builder.add_node("arch_gate",     arch_gate_node)
    builder.add_node("implement",     implement_with_retry)
    builder.add_node("test",          test_agent_node)
    builder.add_node("review",        review_agent_node)
    builder.add_node("deploy_local",  deploy_local_agent_node)
    builder.add_node("e2e_local",     e2e_local_agent_node)
    builder.add_node("deploy_cloud",  deploy_cloud_agent_node)
    builder.add_node("e2e_cloud",     e2e_cloud_agent_node)

    # ── Entry: intake classifies and routes ────────────────────────────────────
    builder.set_entry_point("intake")
    builder.add_conditional_edges(
        "intake", route_after_intake,
        {
            "confluence": "confluence",
            "design":     "design",
            "implement":  "implement",
        },
    )

    # ── Fresh-idea path: confluence → stories → PO gate ───────────────────────
    builder.add_edge("confluence", "stories")
    builder.add_edge("stories",    "po_gate")
    builder.add_conditional_edges("po_gate", route_after_po_gate,
                                  {"design": "design", "stories": "stories"})

    # ── Design → Arch gate (shared by fresh_idea / existing_story / spike) ────
    builder.add_edge("design", "arch_gate")
    builder.add_conditional_edges("arch_gate", route_after_arch_gate,
                                  {"implement": "implement", "design": "design"})

    # ── Implement → test → review (shared by all paths) ───────────────────────
    builder.add_edge("implement", "test")
    builder.add_conditional_edges("test",   route_after_test,
                                  {"review": "review", "implement": "implement"})
    builder.add_conditional_edges("review", route_after_review,
                                  {"deploy_local": "deploy_local", "implement": "implement"})

    # ── Two-tier E2E ───────────────────────────────────────────────────────────
    builder.add_edge("deploy_local", "e2e_local")
    builder.add_conditional_edges("e2e_local",    route_after_e2e_local,
                                  {"deploy_cloud": "deploy_cloud", END: END})
    builder.add_conditional_edges("deploy_cloud", route_after_deploy_cloud,
                                  {"e2e_cloud": "e2e_cloud", END: END})
    builder.add_edge("e2e_cloud", END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["po_gate", "arch_gate"],
    )


graph = build_graph()
