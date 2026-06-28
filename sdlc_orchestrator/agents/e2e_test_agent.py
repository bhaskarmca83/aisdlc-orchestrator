"""sdlc_orchestrator/agents/e2e_test_agent.py
Generates and executes Playwright E2E tests against the deployed environment.
"""
import json
from langchain_core.messages import HumanMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.tools.browser_tools import (
    run_playwright_test, check_page_loads, generate_playwright_spec,
)
from sdlc_orchestrator.tools.jira_tools import add_comment

SYSTEM_PROMPT = """You are a Senior QA Automation Engineer.
Generate comprehensive Playwright TypeScript E2E tests for the feature below.
Each test should use page.goto, click, fill, expect assertions.
Return a single TypeScript spec file content (no FILE: header needed)."""


async def e2e_test_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("e2e", state):
        emit(EventType.INFO, "E2ETestAgent starting — running Playwright E2E tests")

        mem = SharedMemory(state["project_id"])
        await mem.init()

        ctx       = await mem.get_project_context()
        learnings = await mem.get_accumulated_learnings(limit=5)

        deploy_status = state.get("deploy_status", {})
        env_urls      = state.get("env_urls", {})
        base_url      = env_urls.get("dev", "http://localhost:8080")

        if not any(v == "success" for v in deploy_status.values()):
            emit(EventType.INFO, "No successful deploy — skipping E2E tests")
            return {
                **state,
                "e2e_results":   {"skipped": True, "reason": "no_successful_deploy"},
                "current_stage": "e2e",
            }

        # Health check
        health = await check_page_loads(base_url)
        if not health["ok"]:
            emit(EventType.ERROR, f"App not reachable at {base_url}: {health}")
            return {
                **state,
                "e2e_results":   {"passed": False, "error": f"App not reachable: {health}"},
                "current_stage": "e2e",
            }
        emit(EventType.INFO, f"App healthy at {base_url} (status={health['status']})")

        llm = ProviderFactory.get_model(AgentRole.E2E)

        stories = state.get("stories", [])
        story_ctx = "\n".join(
            f"- {s.get('summary', '')} | AC: {'; '.join(s.get('acceptance_criteria', []))}"
            for s in stories[:10]
        )

        learning_ctx = ""
        if learnings:
            learning_ctx = "\nPrevious E2E patterns:\n" + json.dumps(
                [l["content"] for l in learnings[:3]], indent=2
            )

        prompt = (
            f"{SYSTEM_PROMPT}{learning_ctx}\n\n"
            f"Base URL: {base_url}\n"
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n\n"
            f"Stories to test:\n{story_ctx}"
        )

        emit(EventType.LLM, "Calling LLM to generate E2E spec")
        response  = await llm.ainvoke([HumanMessage(content=prompt)])
        spec_code = response.content

        # Strip markdown fences if LLM wrapped in code block
        if "```" in spec_code:
            import re
            m = re.search(r"```(?:typescript|ts)?\n(.*?)```", spec_code, re.DOTALL)
            if m:
                spec_code = m.group(1)

        emit(EventType.INFO, f"Running Playwright spec ({len(spec_code)} chars)")
        test_result = await run_playwright_test(spec_code, test_file_name="e2e_sdlc.spec.ts")

        passed     = test_result.get("passed", False)
        e2e_suite  = list(state.get("e2e_test_suite", [])) + [spec_code]

        emit(EventType.INFO if passed else EventType.ERROR,
             f"E2E tests {'PASSED' if passed else 'FAILED'}")

        # Comment on Jira stories
        for story in stories:
            if story.get("jira_key"):
                try:
                    status_text = "PASSED" if passed else "FAILED"
                    await add_comment(
                        story["jira_key"],
                        f"E2E tests {status_text} against {base_url}",
                    )
                except Exception:
                    pass

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "e2e",
            "learning_type":"e2e_results",
            "content":      {
                "passed":   passed,
                "base_url": base_url,
                "returncode": test_result.get("returncode", -1),
            },
            "metadata": {},
        })

        emit(EventType.DONE, f"E2E complete — passed={passed}")

        return {
            **state,
            "e2e_results":   test_result,
            "e2e_test_suite": e2e_suite,
            "current_stage": "e2e",
        }
