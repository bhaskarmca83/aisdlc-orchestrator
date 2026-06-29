"""sdlc_orchestrator/api/validate.py
Validates Jira project keys and Confluence space keys against the real Atlassian instance.
Also handles Confluence space creation for new projects.
"""
import os
import base64
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/validate", tags=["validate"])

_JIRA_BASE = os.environ.get("JIRA_BASE_URL", "https://bhaskarwork.atlassian.net")
_CONF_BASE = os.environ.get("CONFLUENCE_BASE_URL", "https://bhaskarwork.atlassian.net/wiki")
_EMAIL     = os.environ.get("JIRA_EMAIL", "")
_TOKEN     = os.environ.get("JIRA_API_TOKEN", "")


def _auth_headers() -> dict:
    creds = base64.b64encode(f"{_EMAIL}:{_TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


# ─── Jira ────────────────────────────────────────────────────────────────────

@router.get("/jira/{project_key}")
async def validate_jira_project(project_key: str):
    """Returns project info if key exists, 404 if not."""
    url = f"{_JIRA_BASE}/rest/api/3/project/{project_key.upper()}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=_auth_headers())
    if resp.status_code == 200:
        data = resp.json()
        return {
            "exists": True,
            "key":    data.get("key"),
            "name":   data.get("name"),
            "type":   data.get("projectTypeKey"),
            "url":    f"{_JIRA_BASE}/jira/software/projects/{data.get('key')}/boards",
        }
    if resp.status_code == 404:
        return {"exists": False, "key": project_key.upper()}
    if resp.status_code in (401, 403):
        return {
            "exists":     False,
            "key":        project_key.upper(),
            "auth_error": True,
            "error":      f"Jira credentials invalid (HTTP {resp.status_code}). "
                          "Update JIRA_EMAIL and JIRA_API_TOKEN in the server .env and restart.",
        }
    raise HTTPException(status_code=502, detail=f"Jira API error: {resp.status_code}")


# ─── Confluence ───────────────────────────────────────────────────────────────

@router.get("/confluence/{space_key}")
async def validate_confluence_space(space_key: str):
    """Returns space info if key exists, 404 if not."""
    url = f"{_CONF_BASE}/rest/api/space/{space_key.upper()}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=_auth_headers())
    if resp.status_code == 200:
        data = resp.json()
        return {
            "exists":     True,
            "key":        data.get("key"),
            "name":       data.get("name"),
            "homepage_id": data.get("homepage", {}).get("id", ""),
            "url":        f"{_CONF_BASE}/spaces/{data.get('key')}",
        }
    if resp.status_code == 404:
        return {"exists": False, "key": space_key.upper()}
    if resp.status_code in (401, 403):
        return {
            "exists":     False,
            "key":        space_key.upper(),
            "auth_error": True,
            "error":      f"Confluence credentials invalid (HTTP {resp.status_code}). "
                          "Update JIRA_EMAIL and JIRA_API_TOKEN in the server .env and restart.",
        }
    raise HTTPException(status_code=502, detail=f"Confluence API error: {resp.status_code}")


# ─── Confluence Space Creation ────────────────────────────────────────────────

class CreateSpaceRequest(BaseModel):
    key:         str    # e.g. "PAY"
    name:        str    # e.g. "Payment Gateway"
    description: str = ""


@router.post("/confluence/create-space")
async def create_confluence_space(req: CreateSpaceRequest):
    """Creates a new Confluence space. Used during new project registration."""
    url = f"{_CONF_BASE}/rest/api/space"
    payload = {
        "key":  req.key.upper(),
        "name": req.name,
        "description": {
            "plain": {
                "value":          req.description or f"Space for the {req.name} project.",
                "representation": "plain",
            }
        },
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=_auth_headers(), json=payload)

    if resp.status_code in (200, 201):
        data = resp.json()
        return {
            "created":    True,
            "key":        data.get("key"),
            "name":       data.get("name"),
            "homepage_id": data.get("homepage", {}).get("id", ""),
            "url":        f"{_CONF_BASE}/spaces/{data.get('key')}",
        }
    if resp.status_code == 409:
        raise HTTPException(status_code=409, detail=f"Space '{req.key.upper()}' already exists.")
    raise HTTPException(
        status_code=502,
        detail=f"Confluence space creation failed ({resp.status_code}): {resp.text[:200]}"
    )
