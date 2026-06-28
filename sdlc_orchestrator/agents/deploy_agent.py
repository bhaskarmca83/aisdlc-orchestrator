"""sdlc_orchestrator/agents/deploy_agent.py
Deploy agent: generates Terraform + GitHub Actions plans via LLM.
No MCP tools required — uses LLM + state context only.
"""
import json
import re
import os
from langchain_core.messages import HumanMessage, SystemMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage

DEPLOY_ENV = os.environ.get("DEPLOY_ENV", "dev")

SYSTEM_PROMPT = """You are a Senior DevOps Engineer specialising in Kubernetes, Terraform, and CI/CD.

Your task:
1. Generate a Terraform plan for the infra changes needed by the stories
2. Generate GitHub Actions workflow YAML to build, test, and deploy
3. Identify any infra drift or risks

Respond with ONLY valid JSON:
{
  "terraform_plan": {
    "resources": [{"type": "...", "name": "...", "action": "create|update|destroy"}],
    "estimated_cost_usd": 0.0,
    "risks": ["..."]
  },
  "github_actions": {
    "workflow_name": "...",
    "yaml": "..."
  },
  "deployment_steps": ["..."],
  "rollback_plan": "...",
  "environment": "dev|staging|prod"
}"""


async def deploy_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("deploy", state):
        emit(EventType.INFO, f"DeployAgent starting — target env: {DEPLOY_ENV}")

        mem = SharedMemory(state["project_id"])
        await mem.init()
        learnings = await mem.get_accumulated_learnings(limit=5)

        review_result = state.get("review_result", {})
        if review_result.get("verdict") == "REQUEST_CHANGES":
            emit(EventType.INFO, "Review requested changes — skipping deploy")
            return {
                **state,
                "deploy_status": {DEPLOY_ENV: "skipped"},
                "current_stage": "deploy",
            }

        llm = ProviderFactory.get_model(AgentRole.DEPLOY)

        stories       = state.get("stories", [])
        files_changed = state.get("files_changed", [])

        stories_text  = "\n".join(f"- [{s.get('jira_key','N/A')}] {s.get('summary','')}" for s in stories)
        files_text    = json.dumps(files_changed[:10], indent=2)
        learning_ctx  = json.dumps([l["content"] for l in learnings[:3]], indent=2) if learnings else ""

        user_message = (
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
            f"Target environment: {DEPLOY_ENV}\n"
            f"Review score: {review_result.get('score', 0)}\n"
            f"Test coverage: {json.dumps(state.get('test_coverage_map', {}))}\n\n"
            f"Stories:\n{stories_text}\n\n"
            f"Files changed:\n{files_text}\n"
            + (f"\nPrior deployment patterns:\n{learning_ctx}" if learning_ctx else "")
        )

        emit(EventType.LLM, "Calling LLM for deployment plan")
        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ])
        raw = response.content

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            result = json.loads(m.group()) if m else {"deployment_steps": [], "environment": DEPLOY_ENV}

        deploy_status  = dict(state.get("deploy_status", {}))
        deploy_history = list(state.get("deploy_history", []))
        deploy_status[DEPLOY_ENV] = "planned"
        deploy_history.append({"env": DEPLOY_ENV, "status": "planned",
                                "resources": len(result.get("terraform_plan", {}).get("resources", []))})

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "deploy",
            "learning_type":"deploy_plan",
            "content":      {"environment": DEPLOY_ENV,
                             "resource_count": len(result.get("terraform_plan", {}).get("resources", []))},
            "metadata":     {},
        })

        emit(EventType.DONE, f"Deploy plan ready for env={DEPLOY_ENV}")
        return {
            **state,
            "deployment_config": result,
            "deploy_status":     deploy_status,
            "deploy_history":    deploy_history,
            "env_urls":          {**state.get("env_urls", {}), DEPLOY_ENV: f"https://{DEPLOY_ENV}.aisdlc.internal"},
            "current_stage":     "deploy",
        }
