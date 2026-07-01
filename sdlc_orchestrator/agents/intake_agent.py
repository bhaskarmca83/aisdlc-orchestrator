"""sdlc_orchestrator/agents/intake_agent.py
First node in every pipeline run.

Classifies the raw input without asking the user — reads Jira issue type, URL
patterns, and plain-text signals — then populates state so downstream nodes
start at the right stage:

  fresh_idea      → confluence → stories → gates → implement → ...
  existing_story  → design → arch_gate → implement → ...
  defect          → implement → test → review → ...
  spike           → design → arch_gate → implement → ...
"""
import json
import re
from langchain_core.messages import HumanMessage, SystemMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.mcp.client import mcp_manager
from sdlc_orchestrator.agents._utils import find_mcp_tool, parse_mcp_key

_JIRA_KEY_RE = re.compile(r'\b([A-Z][A-Z0-9]+-\d+)\b')
_JIRA_URL_RE = re.compile(r'https?://[^/\s]+/browse/([A-Z][A-Z0-9]+-\d+)')
_CONF_URL_RE = re.compile(r'https?://[^/\s]+/wiki/spaces/')

# Jira issue type → entry_type mapping
_ISSUE_TYPE_MAP = {
    "bug":            "defect",
    "defect":         "defect",
    "story":          "existing_story",
    "user story":     "existing_story",
    "spike":          "spike",
    "technical spike":"spike",
    "epic":           "fresh_idea",   # treat epic as fresh idea — full pipeline
    "task":           "existing_story",
    "sub-task":       "existing_story",
}

CLASSIFY_PROMPT = """You are a Senior Engineering Manager. Classify the following input.

Respond with ONLY valid JSON — no prose:
{
  "entry_type": "fresh_idea" | "existing_story" | "defect" | "spike",
  "summary": "<one-line summary of what the input is asking>",
  "signals": ["<signal1>", "<signal2>"]
}

Rules:
- "defect": mentions a bug, error, crash, broken behaviour, regression, "not working", "fails to", "returns 500"
- "spike": mentions research, investigation, proof-of-concept, POC, evaluate, compare options
- "existing_story": references a specific user story, feature, or task that is already defined
- "fresh_idea": everything else — a new product idea, feature request, or plain English description"""


def _extract_jira_key(text: str) -> str | None:
    m = _JIRA_URL_RE.search(text) or _JIRA_KEY_RE.search(text)
    return m.group(1) if m else None


def _parse_jira_issue(raw) -> dict:
    """Extract fields from MCP Jira get-issue response."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    fields = raw.get("fields", raw)  # some MCP wrappers flatten fields
    return {
        "key":        raw.get("key", ""),
        "summary":    fields.get("summary", ""),
        "description": (fields.get("description") or ""),
        "issue_type": (fields.get("issuetype") or {}).get("name", "story").lower(),
        "priority":   (fields.get("priority")   or {}).get("name", "Medium"),
        "story_points": fields.get("story_points") or fields.get("customfield_10016") or 3,
        "labels":     fields.get("labels", []),
    }


def _acs_from_description(description: str) -> list[str]:
    """Extract acceptance-criteria lines from Jira description text."""
    acs = []
    in_ac_section = False
    for line in description.splitlines():
        stripped = line.strip().lstrip("*-• ")
        if re.search(r'acceptance.criteri|given.when.then|ac:', stripped, re.I):
            in_ac_section = True
            continue
        if in_ac_section and stripped:
            acs.append(stripped)
        if in_ac_section and not stripped:
            break  # blank line ends section
    # Fallback: any Given/When/Then lines anywhere
    if not acs:
        acs = [l.strip().lstrip("*-• ")
               for l in description.splitlines()
               if re.match(r'(given|when|then)\b', l.strip(), re.I)]
    return acs or [f"Given the work is done, When verified, Then it meets the stated goal"]


def _build_story_from_issue(issue: dict) -> dict:
    acs = _acs_from_description(issue["description"])
    return {
        "jira_key":           issue["key"],
        "summary":            issue["summary"],
        "description":        issue["description"],
        "acceptance_criteria": acs,
        "story_points":       issue["story_points"],
        "priority":           issue["priority"],
        "tags":               issue["labels"],
        "repos":              [],  # resolved by resolve_repos in downstream agents
    }


def _build_defect_story(summary: str, detail: str) -> dict:
    return {
        "jira_key":           "DEFECT-TBD",
        "summary":            f"Fix: {summary[:120]}",
        "description":        detail,
        "acceptance_criteria": [
            f"Given the defect described in the input, When the fix is applied, Then the issue no longer occurs",
            "Given the fix, When regression tests run, Then no existing functionality is broken",
        ],
        "story_points":       3,
        "priority":           "High",
        "tags":               ["defect", "bug"],
        "repos":              [],
    }


async def intake_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("intake", state):
        idea_raw = state.get("idea_raw", "").strip()
        emit(EventType.INFO, f"IntakeAgent classifying input ({len(idea_raw)} chars)")

        llm   = ProviderFactory.get_model(AgentRole.CONFLUENCE)   # lightweight model
        tools = mcp_manager.get_tools_for_agent("stories")         # Jira MCP tools

        # ── Step 1: check for a Jira issue key in the input ──────────────────
        jira_key  = _extract_jira_key(idea_raw)
        jira_issue: dict = {}

        if jira_key and tools:
            get_tool = find_mcp_tool(tools, "get", "issue") or find_mcp_tool(tools, "jira", "issue")
            if get_tool:
                emit(EventType.TOOL, f"Fetching Jira issue {jira_key}")
                try:
                    raw   = await get_tool.ainvoke({"issue_key": jira_key})
                    jira_issue = _parse_jira_issue(raw)
                    emit(EventType.TOOL, f"Fetched: [{jira_key}] {jira_issue.get('summary','')} "
                                         f"(type={jira_issue.get('issue_type','')})")
                except Exception as e:
                    emit(EventType.ERROR, f"Jira fetch failed for {jira_key}: {e}")

        # ── Step 2: determine entry_type ─────────────────────────────────────
        entry_type = ""
        llm_summary = ""

        if jira_issue:
            # Trust the Jira issue type over LLM text classification
            raw_type = jira_issue.get("issue_type", "story")
            entry_type = _ISSUE_TYPE_MAP.get(raw_type, "existing_story")
            llm_summary = jira_issue.get("summary", "")
            emit(EventType.INFO, f"Classified from Jira issuetype '{raw_type}' → {entry_type}")
        else:
            # No Jira issue: LLM classifies plain text
            resp = await llm.ainvoke([
                SystemMessage(content=CLASSIFY_PROMPT),
                HumanMessage(content=f"Input:\n{idea_raw[:2000]}"),
            ])
            try:
                cls = json.loads(resp.content)
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", resp.content, re.DOTALL)
                cls = json.loads(m.group()) if m else {}
            entry_type  = cls.get("entry_type", "fresh_idea")
            llm_summary = cls.get("summary", "")
            emit(EventType.INFO, f"LLM classified input as '{entry_type}': {llm_summary}")

        # ── Step 3: build stories list for non-fresh-idea entry types ────────
        stories      = state.get("stories", [])
        requirements = state.get("requirements", [])

        if entry_type in ("existing_story", "spike") and jira_issue:
            story = _build_story_from_issue(jira_issue)
            stories = [story]
            requirements = [story["summary"]] + [f"AC: {ac}" for ac in story["acceptance_criteria"]]
            emit(EventType.INFO, f"Loaded story [{jira_issue['key']}] from Jira")

        elif entry_type == "defect":
            if jira_issue:
                story = _build_defect_story(jira_issue["summary"], jira_issue["description"])
                story["jira_key"] = jira_issue["key"]
                story["priority"] = jira_issue.get("priority", "High")
            else:
                story = _build_defect_story(llm_summary or idea_raw[:120], idea_raw)
            stories = [story]
            requirements = [story["summary"]]
            emit(EventType.INFO, "Built defect story from input — routing to implementation")

        emit(EventType.DONE, f"Intake complete: entry_type={entry_type}, stories={len(stories)}")
        return {
            **state,
            "entry_type":   entry_type,
            "stories":      stories,
            "requirements": requirements,
            "current_stage": "intake",
        }
