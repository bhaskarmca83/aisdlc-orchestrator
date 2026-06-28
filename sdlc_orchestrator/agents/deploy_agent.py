"""sdlc_orchestrator/agents/deploy_agent.py
Deploys to EKS using Helm, monitors rollout, triggers rollback on failure.
"""
import json
import os
from langchain_core.messages import HumanMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.tools.deploy_tools import (
    helm_upgrade, helm_rollback, get_pod_status, wait_for_rollout,
)
from sdlc_orchestrator.tools.jira_tools import update_story_status, add_comment

HELM_CHART_DIR  = os.environ.get("HELM_CHART_PATH", "../aisdlc-infra/helm/aisdlc-orchestrator")
DEPLOY_ENV      = os.environ.get("DEPLOY_ENV", "dev")
IMAGE_TAG_PREFIX = os.environ.get("IMAGE_TAG_PREFIX", "latest")


async def deploy_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("deploy", state):
        emit(EventType.INFO, f"DeployAgent starting — target env: {DEPLOY_ENV}")

        mem = SharedMemory(state["project_id"])
        await mem.init()

        ctx       = await mem.get_project_context()
        learnings = await mem.get_accumulated_learnings(limit=5)

        review_result = state.get("review_result", {})
        if review_result.get("verdict") == "REQUEST_CHANGES":
            emit(EventType.INFO, "Review requested changes — skipping deploy")
            return {
                **state,
                "deploy_status": {"dev": "skipped", "reason": "review_requested_changes"},
                "current_stage": "deploy",
            }

        llm = ProviderFactory.get_model(AgentRole.DEPLOY)

        # Ask LLM to produce a deploy plan
        prompt = (
            "You are a DevOps engineer. Given the deploy context, return a JSON deploy plan:\n"
            "{\n"
            '  "image_tag": "...",\n'
            '  "values_file": "values-dev.yaml | values-staging.yaml | values-prod.yaml",\n'
            '  "release_name": "aisdlc-orchestrator",\n'
            '  "namespace": "aisdlc",\n'
            '  "notes": "..."\n'
            "}\n\n"
            f"Deploy env: {DEPLOY_ENV}\n"
            f"Files changed: {len(state.get('files_changed', []))}\n"
            f"Review score: {review_result.get('score', 0)}\n"
            f"Test coverage: {json.dumps(state.get('test_coverage_map', {}))}"
        )

        emit(EventType.LLM, "Calling LLM for deploy plan")
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw      = response.content

        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            plan = json.loads(m.group()) if m else {}

        image_tag   = plan.get("image_tag", IMAGE_TAG_PREFIX)
        values_file = f"{HELM_CHART_DIR}/{plan.get('values_file', f'values-{DEPLOY_ENV}.yaml')}"
        release     = plan.get("release_name", "aisdlc-orchestrator")
        namespace   = plan.get("namespace", "aisdlc")

        emit(EventType.INFO, f"Deploying {release}:{image_tag} to {namespace} via {values_file}")

        deploy_result = await helm_upgrade(
            release=release,
            values_file=values_file,
            image_tag=image_tag,
            namespace=namespace,
        )

        deploy_status: dict[str, str] = dict(state.get("deploy_status", {}))
        deploy_history = list(state.get("deploy_history", []))

        if deploy_result["success"]:
            emit(EventType.DONE, f"Helm upgrade succeeded for {release}")

            # Wait for pods
            rollout = await wait_for_rollout(release, namespace=namespace, timeout_seconds=180)
            if not rollout["success"]:
                emit(EventType.ERROR, "Rollout did not complete — triggering rollback")
                await helm_rollback(release, namespace=namespace)
                deploy_status[DEPLOY_ENV] = "rollback"
            else:
                pods = await get_pod_status(namespace)
                emit(EventType.INFO, f"Pods: {[p['name'] + '=' + p['phase'] for p in pods]}")
                deploy_status[DEPLOY_ENV] = "success"
        else:
            emit(EventType.ERROR, f"Helm upgrade failed: {deploy_result.get('stderr', '')}")
            await helm_rollback(release, namespace=namespace)
            deploy_status[DEPLOY_ENV] = "failed"

        deploy_history.append({
            "env":        DEPLOY_ENV,
            "image_tag":  image_tag,
            "status":     deploy_status[DEPLOY_ENV],
            "stdout":     deploy_result.get("stdout", "")[:500],
        })

        # Update Jira stories
        for story in state.get("stories", []):
            if story.get("jira_key"):
                try:
                    new_status = "Done" if deploy_status[DEPLOY_ENV] == "success" else "In Progress"
                    await update_story_status(story["jira_key"], new_status)
                    await add_comment(
                        story["jira_key"],
                        f"Deployed to {DEPLOY_ENV}: {deploy_status[DEPLOY_ENV]} (image: {image_tag})",
                    )
                except Exception as e:
                    emit(EventType.ERROR, f"Jira update failed: {e}")

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "deploy",
            "learning_type":"deploy_result",
            "content":      {"env": DEPLOY_ENV, "status": deploy_status[DEPLOY_ENV], "image_tag": image_tag},
            "metadata":     {},
        })

        return {
            **state,
            "deploy_status":  deploy_status,
            "deploy_history": deploy_history,
            "env_urls":       {**state.get("env_urls", {}), DEPLOY_ENV: f"https://{DEPLOY_ENV}.aisdlc.internal"},
            "current_stage":  "deploy",
        }
