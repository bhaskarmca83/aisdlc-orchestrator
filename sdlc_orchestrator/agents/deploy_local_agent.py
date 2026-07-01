"""sdlc_orchestrator/agents/deploy_local_agent.py
Local deploy agent: health-checks a locally running app (docker-compose / dev server).
Sets local_deployment_url when the app is reachable; skips with reason when cloud-only
dependencies are detected or the app is not running locally.
"""
import os
import asyncio
import httpx

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage

# Cloud services that make local deploy impractical
_CLOUD_ONLY = {
    "cognito", "sqs", "sns", "dynamodb", "aurora", "eks", "ecs fargate",
    "lambda", "kinesis", "glue", "redshift", "step functions",
}

# Health-check paths to probe in order
_HEALTH_PATHS = ["/actuator/health", "/health", "/api/health", "/"]

# How long to wait for the local app to respond
_TIMEOUT_S = int(os.environ.get("LOCAL_HEALTH_TIMEOUT", "5"))
_RETRIES   = int(os.environ.get("LOCAL_HEALTH_RETRIES", "3"))


def _detect_cloud_deps(state: SDLCState) -> list[str]:
    tech      = " ".join(state.get("tech_stack", [])).lower()
    artifacts = state.get("design_artifacts", {})
    arch      = str(artifacts.get("architecture_diagram", "")).lower()
    comps     = " ".join(c.get("tech", "") for c in artifacts.get("component_breakdown", [])).lower()
    combined  = f"{tech} {arch} {comps}"
    return [svc for svc in _CLOUD_ONLY if svc in combined]


def _local_url(state: SDLCState) -> str:
    override = os.environ.get("LOCAL_APP_URL", "").strip()
    if override:
        return override
    tech = " ".join(state.get("tech_stack", [])).lower()
    if any(t in tech for t in ["react", "vue", "angular", "vite"]):
        return "http://localhost:5173"
    if any(t in tech for t in ["fastapi", "flask", "django"]):
        return "http://localhost:8000"
    if any(t in tech for t in ["node", "express", "nestjs"]):
        return "http://localhost:3000"
    return "http://localhost:8080"   # Spring Boot default


async def _health_check(base_url: str) -> tuple[bool, str]:
    """Return (reachable, matched_path). Tries multiple health paths."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for path in _HEALTH_PATHS:
            for attempt in range(_RETRIES):
                try:
                    r = await client.get(f"{base_url}{path}", timeout=_TIMEOUT_S)
                    if r.status_code < 500:
                        return True, path
                except (httpx.ConnectError, httpx.TimeoutException):
                    if attempt < _RETRIES - 1:
                        await asyncio.sleep(1)
    return False, ""


async def deploy_local_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("deploy_local", state):
        emit(EventType.INFO, "DeployLocalAgent starting")

        stage_statuses = dict(state.get("stage_statuses", {}))

        # Skip if review didn't pass
        review_result = state.get("review_result", {})
        if review_result.get("verdict") == "REQUEST_CHANGES":
            emit(EventType.INFO, "Review requested changes — skipping local deploy")
            stage_statuses["deploy_local"] = "skipped"
            return {
                **state,
                "stage_statuses":           stage_statuses,
                "local_deployment_url":     None,
                "local_deploy_skip_reason": "Review requested changes",
                "current_stage":            "deploy_local",
            }

        # Detect cloud-only dependencies
        cloud_deps = _detect_cloud_deps(state)
        if cloud_deps:
            reason = f"Cloud-only dependencies detected: {', '.join(cloud_deps)}"
            emit(EventType.INFO, f"Skipping local deploy — {reason}")
            stage_statuses["deploy_local"] = "skipped"
            return {
                **state,
                "stage_statuses":           stage_statuses,
                "local_deployment_url":     None,
                "local_deploy_skip_reason": reason,
                "current_stage":            "deploy_local",
            }

        base_url = _local_url(state)
        emit(EventType.INFO, f"Health-checking local app at {base_url}")

        reachable, path = await _health_check(base_url)
        if not reachable:
            reason = (
                f"App not reachable at {base_url} — start it locally "
                f"(e.g. docker-compose up or mvn spring-boot:run)"
            )
            emit(EventType.INFO, f"Skipping local E2E — {reason}")
            stage_statuses["deploy_local"] = "skipped"
            return {
                **state,
                "stage_statuses":           stage_statuses,
                "local_deployment_url":     None,
                "local_deploy_skip_reason": reason,
                "current_stage":            "deploy_local",
            }

        emit(EventType.DONE, f"Local app healthy at {base_url}{path}")
        stage_statuses["deploy_local"] = "success"
        return {
            **state,
            "stage_statuses":           stage_statuses,
            "local_deployment_url":     base_url,
            "local_deploy_skip_reason": None,
            "current_stage":            "deploy_local",
        }
