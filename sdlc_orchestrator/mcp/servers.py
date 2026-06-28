"""sdlc_orchestrator/mcp/servers.py
MCP server subprocess configurations for each external integration.
"""
import os
import sys
from pathlib import Path
from mcp import StdioServerParameters


def _atlassian_server() -> StdioServerParameters:
    # Use the installed CLI entry point (mcp-atlassian) in the same venv
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

# Which tools each agent role is allowed to use
AGENT_TOOL_SERVERS = {
    "confluence":  ["atlassian"],
    "stories":     ["atlassian"],
    "design":      ["atlassian"],
    "implement":   ["github"],
    "test":        ["github"],
    "review":      ["github"],
    "deploy":      [],            # deploy uses local subprocess (helm/kubectl)
    "e2e":         ["playwright", "github"],
}
