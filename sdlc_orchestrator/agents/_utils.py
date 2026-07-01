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


def parse_mcp_id(result: Any) -> str:
    """Three-pass parser for MCP page/resource ID: dict → json.loads → regex."""
    if isinstance(result, dict):
        return str(result.get("id", ""))
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("id"):
                return str(parsed["id"])
        except (json.JSONDecodeError, AttributeError):
            pass
        m = re.search(r'"id"\s*:\s*"(\d+)"', result)
        if m:
            return m.group(1)
    return ""


def parse_mcp_key(result: Any) -> Optional[str]:
    """Three-pass parser for Jira issue key (e.g. 'PROJ-42')."""
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
