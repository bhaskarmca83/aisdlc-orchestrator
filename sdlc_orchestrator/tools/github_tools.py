"""sdlc_orchestrator/tools/github_tools.py
GitHub REST API v3 helpers — branch, commit, PR, file operations.
"""
import os
import json
import base64
import httpx

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "bhaskarmca83")
GITHUB_BASE  = "https://api.github.com"

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def create_branch(repo: str, branch_name: str, base_branch: str = "main") -> dict:
    """Create a new branch from base_branch SHA."""
    sha = await get_branch_sha(repo, base_branch)
    url = f"{GITHUB_BASE}/repos/{GITHUB_OWNER}/{repo}/git/refs"
    payload = {"ref": f"refs/heads/{branch_name}", "sha": sha}
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=_headers(), content=json.dumps(payload), timeout=30)
        r.raise_for_status()
        return r.json()


async def get_branch_sha(repo: str, branch: str) -> str:
    url = f"{GITHUB_BASE}/repos/{GITHUB_OWNER}/{repo}/git/ref/heads/{branch}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=_headers(), timeout=20)
        r.raise_for_status()
        return r.json()["object"]["sha"]


async def upsert_file(
    repo: str, path: str, content: str, branch: str, message: str
) -> dict:
    """Create or update a file in a repo on a given branch."""
    encoded = base64.b64encode(content.encode()).decode()
    url = f"{GITHUB_BASE}/repos/{GITHUB_OWNER}/{repo}/contents/{path}"

    # Try to get existing file SHA
    sha = None
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params={"ref": branch}, headers=_headers(), timeout=20)
        if r.status_code == 200:
            sha = r.json().get("sha")

    payload: dict = {"message": message, "content": encoded, "branch": branch}
    if sha:
        payload["sha"] = sha

    async with httpx.AsyncClient() as client:
        r = await client.put(url, headers=_headers(), content=json.dumps(payload), timeout=30)
        r.raise_for_status()
        return r.json()


async def create_pull_request(
    repo: str, title: str, body: str, head: str, base: str = "main"
) -> dict:
    url = f"{GITHUB_BASE}/repos/{GITHUB_OWNER}/{repo}/pulls"
    payload = {"title": title, "body": body, "head": head, "base": base}
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=_headers(), content=json.dumps(payload), timeout=30)
        r.raise_for_status()
        return r.json()


async def get_file_content(repo: str, path: str, ref: str = "main") -> str:
    """Return decoded file content from a repo."""
    url = f"{GITHUB_BASE}/repos/{GITHUB_OWNER}/{repo}/contents/{path}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params={"ref": ref}, headers=_headers(), timeout=20)
        r.raise_for_status()
        encoded = r.json().get("content", "")
        return base64.b64decode(encoded).decode()


async def list_files(repo: str, path: str = "", ref: str = "main") -> list[dict]:
    url = f"{GITHUB_BASE}/repos/{GITHUB_OWNER}/{repo}/contents/{path}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params={"ref": ref}, headers=_headers(), timeout=20)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else [data]


async def merge_pull_request(repo: str, pr_number: int, merge_method: str = "squash") -> dict:
    url = f"{GITHUB_BASE}/repos/{GITHUB_OWNER}/{repo}/pulls/{pr_number}/merge"
    payload = {"merge_method": merge_method}
    async with httpx.AsyncClient() as client:
        r = await client.put(url, headers=_headers(), content=json.dumps(payload), timeout=30)
        r.raise_for_status()
        return r.json()


async def add_pr_comment(repo: str, pr_number: int, body: str) -> dict:
    url = f"{GITHUB_BASE}/repos/{GITHUB_OWNER}/{repo}/issues/{pr_number}/comments"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=_headers(),
                              content=json.dumps({"body": body}), timeout=30)
        r.raise_for_status()
        return r.json()
