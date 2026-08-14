"""tests/test_routing.py
Unit tests for all LangGraph conditional routing functions.
These are pure functions — no LLM, no MCP, no async required.
"""
import pytest
from unittest.mock import patch

# Patch emit so routing functions don't require a live tracker
with patch("sdlc_orchestrator.monitoring.tracker.emit", lambda *a, **kw: None):
    from sdlc_orchestrator.graph import (
        route_after_intake,
        route_after_po_gate,
        route_after_arch_gate,
        route_after_test,
        route_after_review,
        route_after_e2e_local,
        route_after_deploy_cloud,
        MAX_RETRY,
    )
from langgraph.graph import END


def state(**kwargs):
    base = {
        "entry_type": "fresh_idea", "methodology": "scrum",
        "po_revision_reason": None, "arch_revision_reason": None,
        "test_result": {}, "review_result": {},
        "stage_statuses": {}, "e2e_local_results": {},
        "deployment_url": "", "retry_count": 0,
        "current_stage": "test",
    }
    base.update(kwargs)
    return base


# ─── route_after_intake ───────────────────────────────────────────────────────

class TestRouteAfterIntake:
    def test_fresh_idea_goes_to_confluence(self):
        assert route_after_intake(state(entry_type="fresh_idea")) == "confluence"

    def test_existing_story_goes_to_design(self):
        assert route_after_intake(state(entry_type="existing_story")) == "design"

    def test_spike_goes_to_design(self):
        assert route_after_intake(state(entry_type="spike")) == "design"

    def test_defect_goes_to_implement(self):
        assert route_after_intake(state(entry_type="defect")) == "implement"

    def test_unknown_entry_type_defaults_to_confluence(self):
        assert route_after_intake(state(entry_type="")) == "confluence"


# ─── route_after_po_gate ──────────────────────────────────────────────────────

class TestRouteAfterPoGate:
    def test_no_revision_reason_goes_to_design(self):
        assert route_after_po_gate(state(po_revision_reason=None)) == "design"

    def test_empty_revision_reason_goes_to_design(self):
        assert route_after_po_gate(state(po_revision_reason="")) == "design"

    def test_revision_reason_loops_back_to_stories(self):
        assert route_after_po_gate(state(po_revision_reason="Stories too vague")) == "stories"


# ─── route_after_arch_gate ────────────────────────────────────────────────────

class TestRouteAfterArchGate:
    def test_no_revision_reason_goes_to_implement(self):
        assert route_after_arch_gate(state(arch_revision_reason=None)) == "implement"

    def test_empty_revision_reason_goes_to_implement(self):
        assert route_after_arch_gate(state(arch_revision_reason="")) == "implement"

    def test_revision_reason_loops_back_to_design(self):
        assert route_after_arch_gate(state(arch_revision_reason="Missing DB schema")) == "design"


# ─── route_after_test ─────────────────────────────────────────────────────────

class TestRouteAfterTest:
    def test_passed_goes_to_review(self):
        s = state(test_result={"passed": True}, retry_count=0)
        assert route_after_test(s) == "review"

    def test_failed_below_max_retry_re_implements(self):
        s = state(test_result={"passed": False}, retry_count=0)
        assert route_after_test(s) == "implement"

    def test_failed_at_max_retry_goes_to_review_anyway(self):
        s = state(test_result={"passed": False}, retry_count=MAX_RETRY)
        assert route_after_test(s) == "review"

    def test_missing_test_result_defaults_passed(self):
        s = state(test_result={}, retry_count=0)
        assert route_after_test(s) == "implement"


# ─── route_after_review ───────────────────────────────────────────────────────

class TestRouteAfterReview:
    def test_approve_goes_to_deploy_local(self):
        s = state(review_result={"verdict": "APPROVE"}, retry_count=0)
        assert route_after_review(s) == "deploy_local"

    def test_request_changes_below_max_retry_re_implements(self):
        s = state(review_result={"verdict": "REQUEST_CHANGES"}, retry_count=0)
        assert route_after_review(s) == "implement"

    def test_request_changes_at_max_retry_goes_to_deploy_local(self):
        s = state(review_result={"verdict": "REQUEST_CHANGES"}, retry_count=MAX_RETRY)
        assert route_after_review(s) == "deploy_local"

    def test_missing_verdict_defaults_approve(self):
        s = state(review_result={}, retry_count=0)
        assert route_after_review(s) == "deploy_local"


# ─── route_after_e2e_local ────────────────────────────────────────────────────

class TestRouteAfterE2eLocal:
    def test_skipped_proceeds_to_cloud_deploy(self):
        s = state(stage_statuses={"e2e_local": "skipped"}, e2e_local_results={})
        assert route_after_e2e_local(s) == "deploy_cloud"

    def test_passed_proceeds_to_cloud_deploy(self):
        s = state(stage_statuses={}, e2e_local_results={"passed": True})
        assert route_after_e2e_local(s) == "deploy_cloud"

    def test_failed_stops_pipeline(self):
        s = state(stage_statuses={}, e2e_local_results={"passed": False})
        assert route_after_e2e_local(s) == END

    def test_no_result_stops_pipeline(self):
        s = state(stage_statuses={}, e2e_local_results={})
        assert route_after_e2e_local(s) == END


# ─── route_after_deploy_cloud ────────────────────────────────────────────────

class TestRouteAfterDeployCloud:
    def test_skipped_goes_to_end(self):
        s = state(stage_statuses={"deploy_cloud": "skipped"}, deployment_url="")
        assert route_after_deploy_cloud(s) == END

    def test_has_url_goes_to_e2e_cloud(self):
        s = state(stage_statuses={}, deployment_url="https://dev.example.com")
        assert route_after_deploy_cloud(s) == "e2e_cloud"

    def test_no_url_goes_to_end(self):
        s = state(stage_statuses={}, deployment_url="")
        assert route_after_deploy_cloud(s) == END
