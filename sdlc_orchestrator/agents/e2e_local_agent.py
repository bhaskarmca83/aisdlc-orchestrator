"""sdlc_orchestrator/agents/e2e_local_agent.py
Local E2E agent: executes real HTTP calls against the local deployment URL.
Each test case (derived from story ACs at story creation) is translated by the LLM
into an HTTP request spec, then executed with httpx. Results are mapped back to test_cases.
"""
import json
import re
import asyncio
import httpx
from langchain_core.messages import HumanMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage

_TC_PROMPT = """Given this acceptance criteria scenario:
"{scenario}"

Generate the HTTP request to test it. Output ONLY valid JSON, no prose:
{{
  "method": "GET",
  "path": "/api/resource",
  "body": null,
  "headers": {{}},
  "expected_status": 200,
  "expected_body_contains": ""
}}"""


async def _translate_scenario(llm, scenario: str) -> dict:
    resp = await llm.ainvoke([HumanMessage(content=_TC_PROMPT.format(scenario=scenario))])
    raw  = resp.content
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group()) if m else {"method": "GET", "path": "/health", "expected_status": 200}


async def _run_test_case(client: httpx.AsyncClient, base_url: str, tc: dict, llm) -> dict:
    scenario = tc.get("scenario", "")
    try:
        spec = await _translate_scenario(llm, scenario)
    except Exception as e:
        return {"id": tc.get("id"), "story_id": tc.get("story_id"),
                "scenario": scenario, "passed": False, "error": f"LLM translation failed: {e}"}

    method          = spec.get("method", "GET").upper()
    path            = spec.get("path", "/health")
    body            = spec.get("body")
    headers         = spec.get("headers", {})
    expected_status = spec.get("expected_status", 200)
    expected_body   = spec.get("expected_body_contains", "")

    try:
        r = await client.request(
            method, f"{base_url}{path}",
            json=body, headers=headers, timeout=10.0
        )
        status_ok = r.status_code == expected_status
        body_ok   = (expected_body.lower() in r.text.lower()) if expected_body else True
        passed    = status_ok and body_ok
        error_msg = None
        if not status_ok:
            error_msg = f"Expected HTTP {expected_status}, got {r.status_code}"
        elif not body_ok:
            error_msg = f"Body missing '{expected_body}'"
        return {
            "id":          tc.get("id"),
            "story_id":    tc.get("story_id"),
            "scenario":    scenario,
            "request":     f"{method} {base_url}{path}",
            "status_code": r.status_code,
            "passed":      passed,
            "error":       error_msg,
        }
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return {
            "id":          tc.get("id"),
            "story_id":    tc.get("story_id"),
            "scenario":    scenario,
            "request":     f"{method} {base_url}{path}",
            "status_code": None,
            "passed":      False,
            "error":       f"Connection error: {e}",
        }


async def e2e_local_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("e2e_local", state):
        emit(EventType.INFO, "E2ELocalAgent starting")

        stage_statuses = dict(state.get("stage_statuses", {}))
        base_url       = state.get("local_deployment_url", "")

        if not base_url:
            reason = state.get("local_deploy_skip_reason", "local_deployment_url not set")
            emit(EventType.INFO, f"Skipping local E2E — {reason}")
            stage_statuses["e2e_local"] = "skipped"
            return {
                **state,
                "stage_statuses":    stage_statuses,
                "e2e_local_results": {"skipped": True, "reason": reason},
                "current_stage":     "e2e_local",
            }

        test_cases = state.get("test_cases", [])
        if not test_cases:
            emit(EventType.INFO, "No test cases in state — skipping local E2E")
            stage_statuses["e2e_local"] = "skipped"
            return {
                **state,
                "stage_statuses":    stage_statuses,
                "e2e_local_results": {"skipped": True, "reason": "No test cases"},
                "current_stage":     "e2e_local",
            }

        mem = SharedMemory(state["project_id"])
        await mem.init()
        llm = ProviderFactory.get_model(AgentRole.E2E)

        emit(EventType.INFO, f"Running {len(test_cases)} test cases against {base_url}")
        results = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for tc in test_cases:
                result = await _run_test_case(client, base_url, tc, llm)
                results.append(result)
                icon = "✅" if result["passed"] else "❌"
                emit(EventType.INFO, f"  {icon} [{result['id']}] {result.get('request', '')} — {result.get('error') or 'passed'}")

        passed_count = sum(1 for r in results if r["passed"])
        total        = len(results)
        all_passed   = passed_count == total

        e2e_local_results = {
            "passed":       all_passed,
            "tests_run":    total,
            "tests_passed": passed_count,
            "tests_failed": total - passed_count,
            "base_url":     base_url,
            "results":      results,
        }

        stage_statuses["e2e_local"] = "success" if all_passed else "failed"

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "e2e_local",
            "learning_type": "e2e_local_results",
            "content":      {"passed": all_passed, "tests_run": total, "tests_passed": passed_count},
            "metadata":     {},
        })

        emit(EventType.DONE, f"Local E2E: {passed_count}/{total} passed — base_url={base_url}")
        return {
            **state,
            "stage_statuses":    stage_statuses,
            "e2e_local_results": e2e_local_results,
            "e2e_results":       e2e_local_results,  # backward-compat alias
            "current_stage":     "e2e_local",
        }
