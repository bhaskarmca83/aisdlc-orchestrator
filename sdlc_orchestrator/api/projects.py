"""sdlc_orchestrator/api/projects.py
Team project registry — each team registers their config once.
Stored in Redis under project:config:{id}.
"""
import base64
import json
import os
import uuid
from datetime import datetime, timezone

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/projects", tags=["projects"])

_redis: aioredis.Redis = None  # set by main.py on startup


def init_project_router(redis_client: aioredis.Redis):
    global _redis
    _redis = redis_client


# ─── Models ──────────────────────────────────────────────────────────────────

class RepoEntry(BaseModel):
    name: str                # "payment-api"
    role: str = ""           # frontend | backend | service | infra | mobile | streaming | data
    profile_id: str = ""     # tech profile ID — drives code gen + review rules + E2E strategy
    language: str = ""       # auto-filled from profile; can override
    url: str = ""            # GitHub repo URL (optional)
    e2e_strategy: str = ""   # override profile default if needed

class ProjectConfigCreate(BaseModel):
    name: str                       # "Payment Gateway"
    team: str                       # "Payments Team"
    jira_project_key: str           # "PAY"
    confluence_space_key: str       # "PAY"
    repos: list[RepoEntry] = []
    methodology: str = ""           # "scrum" | "kanban" | "other" | "" (auto-detect)

class ProjectConfig(ProjectConfigCreate):
    id: str
    methodology: str = "scrum"
    created_at: str


# ─── Methodology detection ────────────────────────────────────────────────────

async def _detect_methodology(jira_project_key: str) -> str:
    """Query Jira Agile board API to determine scrum vs kanban for a project."""
    base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    email    = os.environ.get("JIRA_EMAIL", "")
    token    = os.environ.get("JIRA_API_TOKEN", "")
    if not (base_url and email and token):
        return "scrum"
    try:
        creds = base64.b64encode(f"{email}:{token}".encode()).decode()
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{base_url}/rest/agile/1.0/board",
                params={"projectKeyOrId": jira_project_key},
                headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
            )
            if r.status_code == 200:
                boards = r.json().get("values", [])
                if boards:
                    board_type = boards[0].get("type", "scrum").lower()
                    return board_type if board_type in ("scrum", "kanban") else "other"
    except Exception:
        pass
    return "scrum"  # safe default


# ─── Redis helpers ────────────────────────────────────────────────────────────

async def _save(cfg: dict) -> None:
    await _redis.set(f"project:config:{cfg['id']}", json.dumps(cfg))
    await _redis.sadd("project:index", cfg["id"])

async def _load(project_id: str) -> dict | None:
    raw = await _redis.get(f"project:config:{project_id}")
    return json.loads(raw) if raw else None

async def _list_all() -> list[dict]:
    ids = await _redis.smembers("project:index")
    configs = []
    for pid in ids:
        raw = await _redis.get(f"project:config:{pid}")
        if raw:
            configs.append(json.loads(raw))
    return sorted(configs, key=lambda c: c.get("created_at", ""))


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.post("", response_model=ProjectConfig, status_code=201)
async def register_project(req: ProjectConfigCreate):
    jira_key    = req.jira_project_key.upper()
    methodology = req.methodology.lower() if req.methodology else await _detect_methodology(jira_key)
    cfg = {
        "id":                   str(uuid.uuid4()),
        "name":                 req.name,
        "team":                 req.team,
        "jira_project_key":     jira_key,
        "confluence_space_key": req.confluence_space_key.upper(),
        "repos":                [r.model_dump() for r in req.repos],
        "methodology":          methodology,
        "created_at":           datetime.now(timezone.utc).isoformat(),
    }
    await _save(cfg)
    return cfg


@router.get("", response_model=list[ProjectConfig])
async def list_projects():
    return await _list_all()


@router.get("/{project_config_id}", response_model=ProjectConfig)
async def get_project(project_config_id: str):
    cfg = await _load(project_config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Project config not found")
    return cfg


@router.delete("/{project_config_id}", status_code=204)
async def delete_project(project_config_id: str):
    await _redis.delete(f"project:config:{project_config_id}")
    await _redis.srem("project:index", project_config_id)
