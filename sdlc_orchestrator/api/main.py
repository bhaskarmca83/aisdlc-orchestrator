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

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

app = FastAPI(title="AI SDLC Orchestrator", version="1.0.0")

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
    project_id: str = ""
    project_name: str = "SDLC Project"
    confluence_page_url: str = ""

class ApproveRequest(BaseModel):
    approved: bool = True
    reason: str = ""


# ─── Pipeline run ─────────────────────────────────────────────────────────────

@app.post("/api/pipeline/run")
async def run_pipeline(req: RunRequest):
    execution_id = str(uuid.uuid4())
    project_id   = req.project_id or str(uuid.uuid4())

    init_tracker(execution_id, story_id="")

    initial_state: SDLCState = {
        "project_id":             project_id,
        "project_name":           req.project_name,
        "tech_stack":             [],
        "code_conventions":       {},
        "architecture_decisions": [],
        "api_contracts":          [],
        "test_framework":         "pytest",
        "repo_registry":          [],
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

def _gate_message(gate: str, values: dict) -> str:
    if gate == "po_gate":
        stories = values.get("stories", [])
        return (f"PO Review: {len(stories)} stories created. "
                f"Review them in Jira and approve to proceed to Technical Design.")
    if gate == "arch_gate":
        tsd_id = values.get("confluence_tsd_page_id", "")
        page_url = (f"https://bhaskarmca83.atlassian.net/wiki/spaces/SD/pages/{tsd_id}"
                    if tsd_id else "Confluence SD space")
        return f"Architect Review: Technical Design doc ready at {page_url}. Approve to start implementation."
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
