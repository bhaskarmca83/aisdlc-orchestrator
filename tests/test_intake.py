"""tests/test_intake.py
Unit tests for intake_agent pure-Python helpers — no LLM, no MCP, no async.
"""
import pytest
from sdlc_orchestrator.agents.intake_agent import (
    _extract_jira_key,
    _ISSUE_TYPE_MAP,
    _acs_from_description,
    _parse_jira_issue,
    _build_story_from_issue,
    _build_defect_story,
)


# ─── _extract_jira_key ────────────────────────────────────────────────────────

class TestExtractJiraKey:
    def test_bare_key_in_text(self):
        assert _extract_jira_key("Fix bug in CTS-42") == "CTS-42"

    def test_jira_browse_url(self):
        assert _extract_jira_key("https://bhaskarwork.atlassian.net/browse/PROJ-99") == "PROJ-99"

    def test_key_at_start(self):
        assert _extract_jira_key("PAY-1 is broken") == "PAY-1"

    def test_multi_char_project(self):
        assert _extract_jira_key("AISDLC-123 implement login") == "AISDLC-123"

    def test_no_key_returns_none(self):
        assert _extract_jira_key("please add login feature") is None

    def test_lowercase_not_matched(self):
        assert _extract_jira_key("fix bug in cts-42") is None

    def test_url_takes_precedence(self):
        result = _extract_jira_key("see PROJ-1 or https://x.atlassian.net/browse/PROJ-99")
        assert result == "PROJ-99"


# ─── _ISSUE_TYPE_MAP ──────────────────────────────────────────────────────────

class TestIssueTypeMap:
    @pytest.mark.parametrize("issue_type,expected", [
        ("bug",            "defect"),
        ("defect",         "defect"),
        ("story",          "existing_story"),
        ("user story",     "existing_story"),
        ("task",           "existing_story"),
        ("sub-task",       "existing_story"),
        ("spike",          "spike"),
        ("technical spike","spike"),
        ("epic",           "fresh_idea"),
    ])
    def test_known_types(self, issue_type, expected):
        assert _ISSUE_TYPE_MAP[issue_type] == expected


# ─── _acs_from_description ────────────────────────────────────────────────────

class TestAcsFromDescription:
    def test_given_when_then_lines(self):
        desc = "Some description.\nGiven a user is logged in, When they click logout, Then they are redirected."
        acs = _acs_from_description(desc)
        assert any("Given" in ac for ac in acs)

    def test_ac_section_header(self):
        desc = "Description text.\n\nAcceptance Criteria\n* User can log in\n* User sees dashboard\n\nOther section"
        acs = _acs_from_description(desc)
        assert "User can log in" in acs
        assert "User sees dashboard" in acs

    def test_fallback_for_no_acs(self):
        desc = "Fix the broken thing. It crashes."
        acs = _acs_from_description(desc)
        assert len(acs) == 1
        assert "verified" in acs[0].lower() or "done" in acs[0].lower() or "goal" in acs[0].lower()

    def test_empty_description(self):
        acs = _acs_from_description("")
        assert isinstance(acs, list)
        assert len(acs) >= 1


# ─── _parse_jira_issue ────────────────────────────────────────────────────────

class TestParseJiraIssue:
    def test_standard_jira_response(self):
        raw = {
            "key": "CTS-42",
            "fields": {
                "summary": "Fix login bug",
                "description": "Users can't log in",
                "issuetype": {"name": "Bug"},
                "priority": {"name": "High"},
                "labels": ["auth"],
            }
        }
        result = _parse_jira_issue(raw)
        assert result["key"] == "CTS-42"
        assert result["summary"] == "Fix login bug"
        assert result["issue_type"] == "bug"
        assert result["priority"] == "High"
        assert result["labels"] == ["auth"]

    def test_json_string_input(self):
        import json
        raw = json.dumps({"key": "X-1", "fields": {"summary": "Test", "issuetype": {"name": "Story"}}})
        result = _parse_jira_issue(raw)
        assert result["key"] == "X-1"
        assert result["issue_type"] == "story"

    def test_empty_dict_returns_defaults(self):
        result = _parse_jira_issue({})
        assert result["key"] == ""
        assert result["summary"] == ""

    def test_invalid_input(self):
        assert _parse_jira_issue("not json {{") == {}


# ─── _build_defect_story ──────────────────────────────────────────────────────

class TestBuildDefectStory:
    def test_has_required_fields(self):
        story = _build_defect_story("Login crash", "Detailed description here")
        assert story["summary"].startswith("Fix:")
        assert "Login crash" in story["summary"]
        assert len(story["acceptance_criteria"]) >= 2
        assert "defect" in story["tags"]
        assert story["priority"] == "High"

    def test_summary_truncated_at_120(self):
        long_summary = "x" * 200
        story = _build_defect_story(long_summary, "detail")
        assert len(story["summary"]) <= 120 + len("Fix: ")


# ─── _build_story_from_issue ──────────────────────────────────────────────────

class TestBuildStoryFromIssue:
    def test_basic_story(self):
        issue = {
            "key": "PAY-5",
            "summary": "Add payment method",
            "description": "Acceptance Criteria\n* Can add card\n* Can delete card\n",
            "issue_type": "story",
            "priority": "Medium",
            "story_points": 5,
            "labels": ["payments"],
        }
        story = _build_story_from_issue(issue)
        assert story["jira_key"] == "PAY-5"
        assert story["summary"] == "Add payment method"
        assert len(story["acceptance_criteria"]) >= 1
        assert story["story_points"] == 5
