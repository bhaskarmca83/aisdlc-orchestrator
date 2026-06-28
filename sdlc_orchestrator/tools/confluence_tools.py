"""sdlc_orchestrator/tools/confluence_tools.py
Atlassian Confluence REST API v2 helpers.
"""
import os
import json
import httpx
from typing import Optional

CONFLUENCE_BASE  = os.environ.get("CONFLUENCE_BASE_URL", "https://bhaskarmca83.atlassian.net/wiki")
CONFLUENCE_EMAIL = os.environ.get("CONFLUENCE_EMAIL", "")
CONFLUENCE_TOKEN = os.environ.get("CONFLUENCE_API_TOKEN", "")
SPACE_KEY        = os.environ.get("CONFLUENCE_SPACE_KEY", "SD")
PARENT_PAGE_ID   = os.environ.get("CONFLUENCE_PARENT_PAGE", "50200578")

def _auth() -> tuple[str, str]:
    return (CONFLUENCE_EMAIL, CONFLUENCE_TOKEN)

def _headers() -> dict:
    return {"Content-Type": "application/json", "Accept": "application/json"}


async def get_page_content(page_id: str) -> dict:
    """Fetch a Confluence page including body storage format."""
    url = f"{CONFLUENCE_BASE}/api/v2/pages/{page_id}"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            url,
            params={"body-format": "storage"},
            auth=_auth(),
            headers=_headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


async def search_pages(query: str, space_key: str = SPACE_KEY, limit: int = 10) -> list[dict]:
    """CQL search for pages in a space."""
    cql = f'space="{space_key}" AND type=page AND text~"{query}"'
    url = f"{CONFLUENCE_BASE}/rest/api/content/search"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            url,
            params={"cql": cql, "limit": limit, "expand": "body.storage"},
            auth=_auth(),
            headers=_headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("results", [])


async def create_page(title: str, body_html: str, parent_id: str = PARENT_PAGE_ID) -> dict:
    """Create a new Confluence page under a parent."""
    payload = {
        "spaceId": await _resolve_space_id(),
        "status": "current",
        "title": title,
        "parentId": parent_id,
        "body": {"representation": "storage", "value": body_html},
    }
    url = f"{CONFLUENCE_BASE}/api/v2/pages"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, auth=_auth(), headers=_headers(),
                              content=json.dumps(payload), timeout=30)
        r.raise_for_status()
        return r.json()


async def update_page(page_id: str, title: str, body_html: str, version: int) -> dict:
    """Update an existing Confluence page."""
    payload = {
        "id": page_id,
        "status": "current",
        "title": title,
        "body": {"representation": "storage", "value": body_html},
        "version": {"number": version},
    }
    url = f"{CONFLUENCE_BASE}/api/v2/pages/{page_id}"
    async with httpx.AsyncClient() as client:
        r = await client.put(url, auth=_auth(), headers=_headers(),
                             content=json.dumps(payload), timeout=30)
        r.raise_for_status()
        return r.json()


async def _resolve_space_id() -> str:
    url = f"{CONFLUENCE_BASE}/api/v2/spaces"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params={"keys": SPACE_KEY}, auth=_auth(),
                             headers=_headers(), timeout=20)
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            return results[0]["id"]
        raise ValueError(f"Space {SPACE_KEY} not found")


async def extract_text_from_page(page: dict) -> str:
    """Strip HTML tags from Confluence storage format body."""
    import re
    body = page.get("body", {}).get("storage", {}).get("value", "")
    return re.sub(r"<[^>]+>", " ", body).strip()
