"""sdlc_orchestrator/agents/deploy_cloud_agent.py
Cloud deploy agent: triggers a GitHub Actions workflow_dispatch on the feature branch
for each repo. Sets deployment_url when the workflow succeeds (or uses env var override).
Falls back to generating a deployment plan if no GitHub MCP tools are available.
"""
import json
import os
import re
import asyncio
from langchain_core.messages import HumanMessage, SystemMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.mcp.client import mcp_manager

DEPLOY_ENV      = os.environ.get("DEPLOY_ENV", "dev")
GITHUB_OWNER    = os.environ.get("GITHUB_OWNER", "bhaskarmca83")
DEPLOY_URL_TMPL = os.environ.get("DEPLOY_TARGET_URL", "")   # e.g. "https://dev.myapp.com"

SYSTEM_PROMPT = """You are a Senior DevOps Engineer.

Generate a GitHub Actions workflow_dispatch trigger plan for deploying the feature to the dev environment.
Respond with ONLY valid JSON:
{
  "workflow_file": ".github/workflows/deploy.yml",
  "inputs": {"environment": "dev", "branch": "feature/..."},
  "deployment_steps": ["build", "push image", "apply helm chart"],
  "estimated_minutes": 8,
  "rollback_plan": "..."
}"""


async def _trigger_workflow(tools, repo: str, branch: str) -> bool:
    """Attempt to trigger workflow_dispatch via GitHub MCP tool."""
    dispatch_tool = next(
        (t for t in tools if "dispatch" in t.name.lower() or "workflow" in t.name.lower()), None
    )
    if not dispatch_tool:
        return False
    try:
        await dispatch_tool.ainvoke({
            "owner":  GITHUB_OWNER,
            "repo":   repo,
            "ref":    branch,
            "workflow_id": "deploy.yml",
            "inputs": {"environment": DEPLOY_ENV},
        })
        return True
    except Exception as e:
        emit(EventType.ERROR, f"workflow_dispatch failed for {repo}: {e}")
        return False


async def deploy_cloud_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("deploy_cloud", state):
        emit(EventType.INFO, f"DeployCloudAgent starting — target env: {DEPLOY_ENV}")

        stage_statuses = dict(state.get("stage_statuses", {}))

        # If local E2E failed (and wasn't skipped), don't deploy to cloud
        e2e_local = state.get("e2e_local_results", {})
        local_skipped = state.get("stage_statuses", {}).get("e2e_local") == "skipped"
        if not local_skipped and e2e_local and not e2e_local.get("passed", True):
            emit(EventType.INFO, "Local E2E failed — skipping cloud deploy")
            stage_statuses["deploy_cloud"] = "skipped"
            return {
                **state,
                "stage_statuses": stage_statuses,
                "current_stage":  "deploy_cloud",
            }

        mem  = SharedMemory(state["project_id"])
        await mem.init()
        llm   = ProviderFactory.get_model(AgentRole.DEPLOY)
        tools = mcp_manager.get_tools_for_agent("deploy")

        feature_branches = state.get("feature_branches", {})
        triggered_repos  = []

        if tools and feature_branches:
            emit(EventType.TOOL, f"Triggering GitHub Actions workflows for {list(feature_branches.keys())}")
            for repo, branch in feature_branches.items():
                success = await _trigger_workflow(tools, repo, branch)
                if success:
                    triggered_repos.append(repo)
                    emit(EventType.TOOL, f"Triggered deploy workflow for {repo}@{branch}")
        else:
            emit(EventType.INFO, "No GitHub MCP tools or no feature branches — generating deploy plan only")

        # Generate deploy plan via LLM regardless (for audit trail)
        stories_text = "\n".join(
            f"- [{s.get('jira_key','N/A')}] {s.get('summary','')}" for s in state.get("stories", [])
        )
        user_message = (
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
            f"Target env: {DEPLOY_ENV}\n"
            f"Feature branches: {json.dumps(feature_branches)}\n\n"
            f"Stories:\n{stories_text}"
        )
        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ])
        raw = response.content
        try:
            deploy_plan = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            deploy_plan = json.loads(m.group()) if m else {"deployment_steps": [], "environment": DEPLOY_ENV}

        # Determine deployment URL
        deployment_url = (
            DEPLOY_URL_TMPL
            or state.get("env_urls", {}).get(DEPLOY_ENV)
            or f"https://{DEPLOY_ENV}.aisdlc.internal"
        )

        deploy_status  = {**state.get("deploy_status", {}), DEPLOY_ENV: "triggered" if triggered_repos else "planned"}
        deploy_history = list(state.get("deploy_history", [])) + [{
            "env": DEPLOY_ENV, "repos": triggered_repos, "status": deploy_status[DEPLOY_ENV],
        }]

        stage_statuses["deploy_cloud"] = "success" if triggered_repos else "planned"

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "deploy_cloud",
            "learning_type": "deploy_cloud",
            "content":      {"environment": DEPLOY_ENV, "triggered_repos": triggered_repos},
            "metadata":     {},
        })

        emit(EventType.DONE, f"Cloud deploy {deploy_status[DEPLOY_ENV]} — url={deployment_url}")
        return {
            **state,
            "deployment_config": deploy_plan,
            "deploy_status":     deploy_status,
            "deploy_history":    deploy_history,
            "deployment_url":    deployment_url,
            "env_urls":          {**state.get("env_urls", {}), DEPLOY_ENV: deployment_url},
            "stage_statuses":    stage_statuses,
            "current_stage":     "deploy_cloud",
        }
