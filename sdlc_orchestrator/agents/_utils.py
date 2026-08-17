"""sdlc_orchestrator/agents/_utils.py
Shared agent utilities: repo resolution, MCP tool lookup, MCP response parsing.
"""
import json
import re
from typing import Any, Optional


def resolve_repos(story: dict) -> list[str]:
    """Map story tags and ACs to affected repositories."""
    tags  = story.get("tags", [])
    ac    = " ".join(story.get("acceptance_criteria", [])).lower()
    repos = []
    if any(t in tags for t in ["api", "backend"]) or "database" in ac:
        repos.append("aisdlc-backend")
    if any(t in tags for t in ["ui", "frontend"]) or any(w in ac for w in ["screen", "page", "form"]):
        repos.append("aisdlc-frontend")
    if any(t in tags for t in ["infra", "terraform"]) or "deploy" in ac:
        repos.append("aisdlc-infra")
    return repos or ["aisdlc-backend"]


def find_mcp_tool(tools: list, *name_parts: str) -> Optional[Any]:
    """Return the first MCP tool whose name contains ALL parts (case-insensitive)."""
    for t in tools:
        name = t.name.lower()
        if all(p in name for p in name_parts):
            return t
    return None


def _unwrap_mcp(result: Any) -> Any:
    """Unwrap langchain-mcp-adapters list-of-content-blocks to the inner text/dict."""
    if isinstance(result, list):
        text = next(
            (b.get("text", "") for b in result
             if isinstance(b, dict) and b.get("type") == "text"),
            "",
        )
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text
    return result


def parse_mcp_id(result: Any) -> str:
    """Parse MCP page/resource ID from dict, JSON string, list-of-blocks, or raw string."""
    result = _unwrap_mcp(result)
    if isinstance(result, dict):
        return str(result.get("id", ""))
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("id"):
                return str(parsed["id"])
        except (json.JSONDecodeError, AttributeError):
            pass
        m = re.search(r'"id"\s*:\s*"?(\d+)"?', result)
        if m:
            return m.group(1)
    return ""


def parse_mcp_key(result: Any) -> Optional[str]:
    """Parse Jira issue key (e.g. 'PROJ-42') from dict, JSON string, or list-of-blocks."""
    result = _unwrap_mcp(result)
    if isinstance(result, dict):
        return result.get("key")
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed.get("key")
        except (json.JSONDecodeError, AttributeError):
            pass
        m = re.search(r'\b([A-Z][A-Z0-9]+-\d+)\b', result)
        if m:
            return m.group(1)
    return None
