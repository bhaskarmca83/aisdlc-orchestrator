"""sdlc_orchestrator/api/projects.py
Team project registry — each team registers their config once.
Stored in Redis under project:config:{id}.
"""
import json
import uuid
from datetime import datetime, timezone

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

class ProjectConfig(ProjectConfigCreate):
    id: str
    created_at: str


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
    cfg = {
        "id":                   str(uuid.uuid4()),
        "name":                 req.name,
        "team":                 req.team,
        "jira_project_key":     req.jira_project_key.upper(),
        "confluence_space_key": req.confluence_space_key.upper(),
        "repos":                [r.model_dump() for r in req.repos],
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
