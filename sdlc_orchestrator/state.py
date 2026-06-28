"""sdlc_orchestrator/state.py
Shared state schema for the entire SDLC pipeline.
"""
from typing import Any, TypedDict, Optional

class SDLCState(TypedDict):
    project_id: str
    project_name: str
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
    idea_raw: str
    requirements: list[str]
    stories: list[dict]
    assigned_repos: list[dict]
    design_artifacts: dict
    approval_payload: Optional[dict]
    files_changed: list[dict]
    feature_branches: dict[str, str]
    test_result: Optional[dict]
    review_result: Optional[dict]
    deploy_status: dict[str, str]
    e2e_results: dict[str, Any]
    patterns_used: list[str]
    bugs_encountered: list[dict]
    test_coverage_map: dict[str, float]
    review_history: list[dict]
    deploy_history: list[dict]
    e2e_test_suite: list[str]
    rollback_events: list[dict]
    execution_id: str
    current_stage: str
    stage_timings: dict[str, float]
    error: Optional[str]
    retry_count: int