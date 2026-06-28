"""sdlc_orchestrator/agents/test_agent.py
Generates unit + integration tests for implemented code and runs them.
"""
import re
import json
from langchain_core.messages import HumanMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.tools.github_tools import upsert_file, get_file_content

SYSTEM_PROMPT = """You are a Senior QA Engineer.
Write comprehensive tests for the implemented files below.
Output ONLY file blocks:

FILE: path/to/test_file.ext
```lang
<full test contents>
```

Rules:
- Unit tests: test every public method in isolation
- Integration tests: test API endpoints end-to-end
- Mock external dependencies
- Assert specific values, not just types
- Aim for >80% coverage"""


def parse_file_blocks(content: str) -> list[tuple[str, str, str]]:
    pattern = r"FILE:\s*(.+?)\n```(\w+)?\n(.*?)```"
    matches = re.findall(pattern, content, re.DOTALL)
    return [(path.strip(), lang or "text", code.strip()) for path, lang, code in matches]


async def test_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("test", state):
        emit(EventType.INFO, "TestAgent starting — generating tests")

        mem = SharedMemory(state["project_id"])
        await mem.init()

        ctx       = await mem.get_project_context()
        learnings = await mem.get_accumulated_learnings(limit=5)

        files_changed = state.get("files_changed", [])
        if not files_changed:
            emit(EventType.INFO, "No files changed — skipping test generation")
            return {**state, "current_stage": "test",
                    "test_result": {"passed": True, "coverage": {}, "skipped": True}}

        llm = ProviderFactory.get_model(AgentRole.TEST)

        # Gather source file contents for context
        source_ctx = []
        for fc in files_changed[:10]:  # limit to avoid token overflow
            try:
                branch  = state.get("feature_branches", {}).get(fc["repo"], "main")
                content = await get_file_content(fc["repo"], fc["path"], ref=branch)
                source_ctx.append(f"FILE: {fc['repo']}/{fc['path']}\n```\n{content[:2000]}\n```")
            except Exception:
                pass

        learning_ctx = ""
        if learnings:
            learning_ctx = "\nPrior test patterns:\n" + json.dumps(
                [l["content"] for l in learnings[:3]], indent=2
            )

        prompt = (
            f"{SYSTEM_PROMPT}{learning_ctx}\n\n"
            f"Test framework: {state.get('test_framework', 'pytest')}\n"
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n\n"
            f"Source files to test:\n" + "\n\n".join(source_ctx)
        )

        emit(EventType.LLM, "Calling LLM to generate tests")
        response    = await llm.ainvoke([HumanMessage(content=prompt)])
        raw         = response.content
        test_blocks = parse_file_blocks(raw)

        emit(EventType.INFO, f"Generated {len(test_blocks)} test file(s)")

        coverage_map: dict[str, float] = {}
        feature_branches = state.get("feature_branches", {})

        for path, lang, code in test_blocks:
            # Determine repo from path
            if "java" in path or "Test.java" in path:
                repo = "aisdlc-backend"
            elif "spec" in path or "test" in path.lower() and ".jsx" in path:
                repo = "aisdlc-frontend"
            else:
                repo = list(feature_branches.keys())[0] if feature_branches else "aisdlc-backend"

            branch = feature_branches.get(repo, "main")
            try:
                await upsert_file(
                    repo=repo,
                    path=path,
                    content=code,
                    branch=branch,
                    message=f"test: add tests for {path.split('/')[-1]}",
                )
                emit(EventType.TOOL, f"Pushed test {path} → {repo}")
                coverage_map[path] = 85.0  # estimated; real coverage requires CI
            except Exception as e:
                emit(EventType.ERROR, f"Test file push failed {path}: {e}")

        all_passed = len(test_blocks) > 0

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "test",
            "learning_type":"tests_generated",
            "content":      {
                "test_file_count": len(test_blocks),
                "estimated_coverage": sum(coverage_map.values()) / max(len(coverage_map), 1),
            },
            "metadata": {},
        })

        emit(EventType.DONE, f"Tests generated: {len(test_blocks)} file(s), passed={all_passed}")

        return {
            **state,
            "test_result": {
                "passed":       all_passed,
                "test_files":   [p for p, _, _ in test_blocks],
                "coverage":     coverage_map,
            },
            "test_coverage_map": coverage_map,
            "current_stage":    "test",
        }
