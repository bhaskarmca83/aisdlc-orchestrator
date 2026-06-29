"""sdlc_orchestrator/api/main.py
FastAPI server: pipeline run, WebSocket event stream, gate approval, status.
"""
import os
import json
import uuid
import asyncio
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sdlc_orchestrator.graph import graph
from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.monitoring.tracker import init_tracker, EventType, emit
from sdlc_orchestrator.mcp.client import mcp_manager
from sdlc_orchestrator.api.projects import router as projects_router, init_project_router, _load as _load_project
from sdlc_orchestrator.api.profiles import router as profiles_router, init_profiles_router
from sdlc_orchestrator.api.validate  import router as validate_router

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

app = FastAPI(title="AI SDLC Orchestrator", version="1.0.0")
app.include_router(projects_router)
app.include_router(profiles_router)
app.include_router(validate_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

redis: aioredis.Redis = None  # type: ignore


@app.on_event("startup")
async def startup():
    global redis
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    init_project_router(redis)
    init_profiles_router(redis)
    try:
        await mcp_manager.start()
    except Exception as e:
        print(f"[startup] MCP manager failed to start (tools unavailable): {e}")


@app.on_event("shutdown")
async def shutdown():
    try:
        await mcp_manager.stop()
    except Exception:
        pass
    if redis:
        await redis.aclose()


# ─── Models ───────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    idea: str
    project_config_id: str = ""      # registered team project — preferred
    # fallback: manual override (used when no project config is registered)
    project_name: str = ""
    confluence_page_url: str = ""
    jira_project_key: str = ""
    confluence_space_key: str = ""

class ApproveRequest(BaseModel):
    approved: bool = True
    reason: str = ""


# ─── Pipeline run ─────────────────────────────────────────────────────────────

@app.post("/api/pipeline/run")
async def run_pipeline(req: RunRequest):
    execution_id = str(uuid.uuid4())

    # Load team project config if a registered config ID was provided
    proj_cfg = None
    if req.project_config_id:
        proj_cfg = await _load_project(req.project_config_id)
        if not proj_cfg:
            raise HTTPException(status_code=404,
                                detail=f"Project config '{req.project_config_id}' not found. Register it first via POST /api/projects")

    project_id           = proj_cfg["id"]       if proj_cfg else str(uuid.uuid4())
    project_name         = proj_cfg["name"]      if proj_cfg else (req.project_name or "SDLC Project")
    jira_project_key     = proj_cfg["jira_project_key"]     if proj_cfg else req.jira_project_key
    confluence_space_key = proj_cfg["confluence_space_key"] if proj_cfg else req.confluence_space_key

    if not jira_project_key or not confluence_space_key:
        raise HTTPException(
            status_code=400,
            detail="Either provide project_config_id (registered team project) or both "
                   "jira_project_key and confluence_space_key explicitly."
        )

    init_tracker(execution_id, story_id="")

    initial_state: SDLCState = {
        "project_id":             project_id,
        "project_name":           project_name,
        "target_jira_project":    jira_project_key,
        "target_confluence_space": confluence_space_key,
        "tech_stack":             [],
        "code_conventions":       {},
        "architecture_decisions": [],
        "api_contracts":          [],
        "test_framework":         "pytest",
        "repo_registry":          proj_cfg.get("repos", []) if proj_cfg else [],
        "env_urls":               {},
        "current_story_id":       "",
        "current_epic_id":        "",
        "confluence_page_url":    req.confluence_page_url,
        "idea_raw":               req.idea,
        "requirements":           [],
        "stories":                [],
        "assigned_repos":         [],
        "design_artifacts":       {},
        "approval_payload":                None,
        "po_approval":                     None,
        "arch_approval":                   None,
        "confluence_requirements_page_id": "",
        "confluence_tsd_page_id":          "",
        "deployment_config":               None,
        "files_changed":                   [],
        "feature_branches":       {},
        "test_result":            None,
        "review_result":          None,
        "deploy_status":          {},
        "e2e_results":            {},
        "patterns_used":          [],
        "bugs_encountered":       [],
        "test_coverage_map":      {},
        "review_history":         [],
        "deploy_history":         [],
        "e2e_test_suite":         [],
        "rollback_events":        [],
        "execution_id":           execution_id,
        "current_stage":          "init",
        "stage_timings":          {},
        "error":                  None,
        "retry_count":            0,
    }

    config = {"configurable": {"thread_id": execution_id}}

    await redis.set(
        f"run:{execution_id}:status",
        json.dumps({"status": "running", "stage": "init"}),
        ex=3600,
    )

    asyncio.create_task(_run_graph(execution_id, initial_state, config))

    return {"execution_id": execution_id, "project_id": project_id, "status": "started"}


async def _run_graph(execution_id: str, state: SDLCState, config: dict):
    try:
        async for event in graph.astream(state, config):
            stage = list(event.keys())[0] if event else "unknown"
            await redis.xadd(
                f"sdlc:events:{execution_id}",
                {"data": json.dumps({"type": "stage_update", "stage": stage, "data": {}})},
                maxlen=1000,
            )
            # Check for interrupt at either gate
            snap = await graph.aget_state(config)
            if snap and snap.next and snap.next[0] in ("po_gate", "arch_gate"):
                gate = snap.next[0]
                await redis.set(
                    f"run:{execution_id}:status",
                    json.dumps({"status": "awaiting_approval", "stage": gate}),
                    ex=3600,
                )
                await redis.xadd(
                    f"sdlc:events:{execution_id}",
                    {"data": json.dumps({"type": "gate", "gate": gate,
                                         "message": _gate_message(gate, snap.values)})},
                    maxlen=1000,
                )
                return  # Pause; /api/gate/{id}/approve will resume

        await redis.set(
            f"run:{execution_id}:status",
            json.dumps({"status": "completed", "stage": "done"}),
            ex=3600,
        )
    except Exception as e:
        await redis.set(
            f"run:{execution_id}:status",
            json.dumps({"status": "error", "error": str(e)}),
            ex=3600,
        )
        await redis.xadd(
            f"sdlc:events:{execution_id}",
            {"data": json.dumps({"type": "error", "message": str(e)})},
        )


# ─── Gate helpers ─────────────────────────────────────────────────────────────

_JIRA_BASE = os.environ.get("JIRA_BASE_URL", "https://bhaskarwork.atlassian.net")
_JIRA_PROJ = os.environ.get("JIRA_PROJECT_KEY", "AISDLC")
_CONF_BASE = os.environ.get("CONFLUENCE_BASE_URL", "https://bhaskarwork.atlassian.net/wiki")


def _gate_message(gate: str, values: dict) -> str:
    if gate == "po_gate":
        stories = values.get("stories", [])
        jira_url = f"{_JIRA_BASE}/jira/software/projects/{_JIRA_PROJ}/boards"
        # Check whether stories are real (non-TBD Jira keys) or in-memory only
        real = [s for s in stories if s.get("jira_key") and "-TBD" not in s.get("jira_key", "")]
        lines = [f"PO Review — {len(stories)} stories generated:"]
        for s in stories:
            key  = s.get("jira_key", "?")
            summ = s.get("summary", "")[:80]
            pts  = s.get("story_points", "?")
            if "-TBD" not in key and key != "?":
                lines.append(f"  [{key}] ({pts}pts) {summ}")
            else:
                lines.append(f"  [in-memory] ({pts}pts) {summ}")
        if real:
            lines.append(f"\nView in Jira: {jira_url}")
        else:
            lines.append("\n⚠️  Jira sync failed — stories are in pipeline memory only. Check server logs.")
        lines.append("\nApprove to proceed to Technical Design.")
        return "\n".join(lines)
    if gate == "arch_gate":
        tsd_id   = values.get("confluence_tsd_page_id", "")
        page_url = (f"{_CONF_BASE}/spaces/SD/pages/{tsd_id}"
                    if tsd_id else f"{_CONF_BASE}/spaces/SD")
        return (f"Architect Review: Technical Design doc ready at {page_url}.\n"
                f"Approve to start implementation.")
    return "Awaiting approval"


# ─── Gate approval ────────────────────────────────────────────────────────────

@app.post("/api/gate/{execution_id}/approve")
async def approve_gate(execution_id: str, req: ApproveRequest):
    config = {"configurable": {"thread_id": execution_id}}
    snap   = await graph.aget_state(config)

    if not snap or not snap.next:
        raise HTTPException(status_code=400, detail="Pipeline is not waiting at a gate")

    active_gate = snap.next[0] if snap.next else None
    if active_gate not in ("po_gate", "arch_gate"):
        raise HTTPException(status_code=400, detail=f"Pipeline is at '{active_gate}', not a gate node")

    # Write the approval into the correct state field
    approval = {"approved": req.approved, "reason": req.reason}
    if active_gate == "po_gate":
        await graph.aupdate_state(config, {"po_approval": approval, "approval_payload": approval})
        next_stage = "design"
    else:
        await graph.aupdate_state(config, {"arch_approval": approval, "approval_payload": approval})
        next_stage = "implement"

    await redis.set(
        f"run:{execution_id}:status",
        json.dumps({"status": "running", "stage": next_stage}),
        ex=3600,
    )

    asyncio.create_task(_resume_graph(execution_id, config))
    return {"execution_id": execution_id, "gate": active_gate, "approved": req.approved}


async def _resume_graph(execution_id: str, config: dict):
    try:
        async for event in graph.astream(None, config):
            stage = list(event.keys())[0] if event else "unknown"
            await redis.xadd(
                f"sdlc:events:{execution_id}",
                {"data": json.dumps({"type": "stage_update", "stage": stage})},
                maxlen=1000,
            )
            # Check if we hit the second gate
            snap = await graph.aget_state(config)
            if snap and snap.next and snap.next[0] in ("po_gate", "arch_gate"):
                gate = snap.next[0]
                await redis.set(
                    f"run:{execution_id}:status",
                    json.dumps({"status": "awaiting_approval", "stage": gate}),
                    ex=3600,
                )
                await redis.xadd(
                    f"sdlc:events:{execution_id}",
                    {"data": json.dumps({"type": "gate", "gate": gate,
                                         "message": _gate_message(gate, snap.values)})},
                    maxlen=1000,
                )
                return

        await redis.set(
            f"run:{execution_id}:status",
            json.dumps({"status": "completed", "stage": "done"}),
            ex=3600,
        )
    except Exception as e:
        await redis.set(
            f"run:{execution_id}:status",
            json.dumps({"status": "error", "error": str(e)}),
            ex=3600,
        )


# ─── WebSocket event stream ────────────────────────────────────────────────────

@app.websocket("/ws/events/{execution_id}")
async def websocket_events(ws: WebSocket, execution_id: str):
    await ws.accept()
    last_id = "0"
    try:
        while True:
            results = await redis.xread(
                {f"sdlc:events:{execution_id}": last_id}, count=50, block=500
            )
            for stream, messages in (results or []):
                for msg_id, data in messages:
                    last_id = msg_id
                    await ws.send_text(data.get("data", "{}"))

            status_raw = await redis.get(f"run:{execution_id}:status")
            if status_raw:
                status = json.loads(status_raw)
                if status.get("status") in ("completed", "error"):
                    await ws.send_text(json.dumps({"type": "done", "status": status}))
                    break
    except WebSocketDisconnect:
        pass


# ─── Status endpoint ──────────────────────────────────────────────────────────

@app.get("/api/pipeline/{execution_id}/status")
async def get_status(execution_id: str):
    raw = await redis.get(f"run:{execution_id}:status")
    if not raw:
        raise HTTPException(status_code=404, detail="Execution not found")
    return json.loads(raw)


@app.get("/api/pipeline/{execution_id}/state")
async def get_state_snapshot(execution_id: str):
    config = {"configurable": {"thread_id": execution_id}}
    snap   = await graph.aget_state(config)
    if not snap:
        raise HTTPException(status_code=404, detail="State not found")
    return {"values": snap.values, "next": list(snap.next)}


@app.get("/health")
async def health():
    return {"status": "ok"}
