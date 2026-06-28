"""sdlc_orchestrator/tools/jira_tools.py
Atlassian Jira Cloud REST API v3 helpers.
"""
import os
import json
import httpx

JIRA_BASE    = os.environ.get("JIRA_BASE_URL", "https://bhaskarmca83.atlassian.net")
JIRA_EMAIL   = os.environ.get("JIRA_EMAIL", "")
JIRA_TOKEN   = os.environ.get("JIRA_API_TOKEN", "")
JIRA_PROJECT = os.environ.get("JIRA_PROJECT_KEY", "CTS")
CLOUD_ID     = os.environ.get("JIRA_CLOUD_ID", "21ffd3eb-a8a6-4b00-86ae-b9a08a8a34e9")

EPIC_MAP = {
    "platform_foundation": "CTS-129",
    "agent_implementation": "CTS-130",
    "monitoring_dashboard": "CTS-131",
    "infrastructure":       "CTS-132",
}

def _auth() -> tuple[str, str]:
    return (JIRA_EMAIL, JIRA_TOKEN)

def _headers() -> dict:
    return {"Content-Type": "application/json", "Accept": "application/json"}


async def create_story(
    summary: str,
    description: str,
    epic_key: str,
    story_points: int = 3,
    labels: list[str] | None = None,
) -> dict:
    """Create a Jira Story under the given epic."""
    payload = {
        "fields": {
            "project":     {"key": JIRA_PROJECT},
            "summary":     summary,
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
            },
            "issuetype":   {"name": "Story"},
            "labels":      labels or [],
            "customfield_10014": epic_key,  # Epic Link
            "customfield_10016": story_points,  # Story Points
        }
    }
    url = f"{JIRA_BASE}/rest/api/3/issue"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, auth=_auth(), headers=_headers(),
                              content=json.dumps(payload), timeout=30)
        r.raise_for_status()
        return r.json()


async def update_story_status(issue_key: str, transition_name: str) -> dict:
    """Transition a Jira issue to a new status."""
    transitions = await get_transitions(issue_key)
    target = next(
        (t for t in transitions if t["name"].lower() == transition_name.lower()), None
    )
    if not target:
        raise ValueError(f"Transition '{transition_name}' not found for {issue_key}")

    url = f"{JIRA_BASE}/rest/api/3/issue/{issue_key}/transitions"
    payload = {"transition": {"id": target["id"]}}
    async with httpx.AsyncClient() as client:
        r = await client.post(url, auth=_auth(), headers=_headers(),
                              content=json.dumps(payload), timeout=30)
        r.raise_for_status()
        return {"issue": issue_key, "transition": transition_name}


async def get_transitions(issue_key: str) -> list[dict]:
    url = f"{JIRA_BASE}/rest/api/3/issue/{issue_key}/transitions"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, auth=_auth(), headers=_headers(), timeout=20)
        r.raise_for_status()
        return r.json().get("transitions", [])


async def add_comment(issue_key: str, comment: str) -> dict:
    payload = {
        "body": {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment}]}],
        }
    }
    url = f"{JIRA_BASE}/rest/api/3/issue/{issue_key}/comment"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, auth=_auth(), headers=_headers(),
                              content=json.dumps(payload), timeout=30)
        r.raise_for_status()
        return r.json()


async def get_issue(issue_key: str) -> dict:
    url = f"{JIRA_BASE}/rest/api/3/issue/{issue_key}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, auth=_auth(), headers=_headers(), timeout=20)
        r.raise_for_status()
        return r.json()


async def search_issues(jql: str, max_results: int = 50) -> list[dict]:
    url = f"{JIRA_BASE}/rest/api/3/search"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            url,
            params={"jql": jql, "maxResults": max_results},
            auth=_auth(),
            headers=_headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("issues", [])
