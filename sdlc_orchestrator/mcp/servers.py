"""sdlc_orchestrator/mcp/servers.py
MCP server subprocess configurations and per-agent tool allowlists.
Each agent only sees the tools it actually needs — not all 73 Atlassian tools.
"""
import os
import sys
from pathlib import Path
from mcp import StdioServerParameters


def _atlassian_server() -> StdioServerParameters:
    cli = str(Path(sys.executable).parent / "mcp-atlassian")
    return StdioServerParameters(
        command=cli,
        args=[],
        env={
            **os.environ,
            "CONFLUENCE_URL":       os.environ.get("CONFLUENCE_BASE_URL", ""),
            "CONFLUENCE_USERNAME":  os.environ.get("CONFLUENCE_EMAIL", ""),
            "CONFLUENCE_API_TOKEN": os.environ.get("CONFLUENCE_API_TOKEN", ""),
            "JIRA_URL":             os.environ.get("JIRA_BASE_URL", ""),
            "JIRA_USERNAME":        os.environ.get("JIRA_EMAIL", ""),
            "JIRA_API_TOKEN":       os.environ.get("JIRA_API_TOKEN", ""),
            "TOOLSETS":             "all",
        },
    )


def _github_server() -> StdioServerParameters:
    return StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={
            **os.environ,
            "GITHUB_PERSONAL_ACCESS_TOKEN": os.environ.get("GITHUB_TOKEN", ""),
        },
    )


def _playwright_server() -> StdioServerParameters:
    return StdioServerParameters(
        command="npx",
        args=["-y", "@playwright/mcp"],
        env={**os.environ},
    )


SERVER_CONFIGS = {
    "atlassian":  _atlassian_server,
    "github":     _github_server,
    "playwright": _playwright_server,
}

# Which MCP servers each agent stage uses
AGENT_TOOL_SERVERS = {
    "confluence":  ["atlassian"],
    "stories":     ["atlassian"],
    "design":      ["atlassian"],
    "implement":   ["github"],
    "test":        ["github"],
    "review":      ["github"],
    "deploy":      [],
    "e2e":         ["playwright", "github"],
}

# Allowlist of tool name substrings per agent — agents only see tools matching these.
# Keeps small local models from being overwhelmed by irrelevant tools.
AGENT_TOOL_FILTER: dict[str, list[str]] = {
    "confluence": [
        "confluence_get_page",
        "confluence_create_page",
        "confluence_update_page",
        "confluence_search",
        "get_confluence_page",
        "create_confluence_page",
        "update_confluence_page",
        "search_confluence",
    ],
    "stories": [
        "create_jira_issue",
        "jira_create_issue",
        "search_jira",
        "jira_search",
        "get_jira_issue",
        "jira_get_issue",
    ],
    "design": [
        "create_confluence_page",
        "confluence_create_page",
        "update_confluence_page",
        "confluence_update_page",
        "add_comment",
        "jira_add_comment",
        "confluence_get_page",
        "get_confluence_page",
    ],
    "implement": [
        "create_branch",
        "push_file",
        "create_pull_request",
        "get_file_contents",
        "create_or_update_file",
        "search_repositories",
    ],
    "test": [
        "push_file",
        "get_file_contents",
        "create_or_update_file",
        "create_branch",
    ],
    "review": [
        "get_pull_request",
        "create_review",
        "list_pull_requests",
        "get_file_contents",
        "create_pull_request_review",
    ],
    "deploy": [],
    "e2e": [
        "navigate",
        "click",
        "screenshot",
        "fill",
        "push_file",
        "create_or_update_file",
        "browser_navigate",
        "browser_click",
        "browser_screenshot",
        "browser_type",
    ],
}
