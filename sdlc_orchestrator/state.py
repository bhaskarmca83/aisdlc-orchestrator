"""sdlc_orchestrator/state.py
Shared state schema for the entire SDLC pipeline.
"""
from typing import Any, TypedDict, Optional

class SDLCState(TypedDict):
    project_id: str
    project_name: str
    # Target Jira project and Confluence space for THIS run (not the AISDLC platform's own)
    target_jira_project: str        # e.g. "CTS" for aisdlc-backend work
    target_confluence_space: str    # e.g. "CCT" for that project's docs
    tech_stack: list[str]
    code_conventions: dict[str, Any]
    architecture_decisions: list[dict]
    api_contracts: list[dict]
    test_framework: str
    repo_registry: list[dict]
    env_urls: dict[str, str]
    current_story_id: str
    current_epic_id: str
    confluence_page_url: str
    confluence_requirements_page_id: str   # requirements page created by confluence agent
    confluence_tsd_page_id: str            # TSD page created by design agent
    idea_raw: str
    requirements: list[str]
    stories: list[dict]
    assigned_repos: list[dict]
    design_artifacts: dict
    # Two separate gate payloads
    po_approval: Optional[dict]            # Gate 1: PO approves stories
    arch_approval: Optional[dict]          # Gate 2: Architect approves TSD
    approval_payload: Optional[dict]       # kept for backward-compat (mirrors active gate)
    files_changed: list[dict]
    feature_branches: dict[str, str]
    test_result: Optional[dict]
    review_result: Optional[dict]
    deploy_status: dict[str, str]
    deployment_config: Optional[dict]
    e2e_results: dict[str, Any]
    patterns_used: list[str]
    bugs_encountered: list[dict]
    test_coverage_map: dict[str, float]
    review_history: list[dict]
    deploy_history: list[dict]
    e2e_test_suite: list[Any]
    rollback_events: list[dict]
    # AC-derived test cases generated at story creation; passed to test_agent + E2E
    test_cases: list[dict]
    # Per-node status for skippable stages: "success" | "skipped" | "failed"
    stage_statuses: dict[str, str]
    # Local deployment URL (docker-compose / local dev server)
    local_deployment_url: Optional[str]
    local_deploy_skip_reason: Optional[str]
    # Cloud deployment URL (dev/qa env after CI/CD deploy)
    deployment_url: Optional[str]
    # E2E results per environment
    e2e_local_results: dict
    e2e_cloud_results: dict
    # Gate revision reasons injected into agent prompts on rejection
    po_revision_reason: Optional[str]
    arch_revision_reason: Optional[str]
    execution_id: str
    current_stage: str
    stage_timings: dict[str, float]
    # Pipeline entry classification (set by intake_agent)
    entry_type: str          # "fresh_idea" | "existing_story" | "defect" | "spike"
    # Execution methodology detected from Jira board at project registration
    methodology: str         # "scrum" | "kanban" | "other"
    error: Optional[str]
    retry_count: int
