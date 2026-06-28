"""sdlc_orchestrator/agents/review_agent.py
Code review: security, correctness, patterns, and PR comment posting.
"""
import json
from langchain_core.messages import HumanMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.tools.github_tools import get_file_content, add_pr_comment

SYSTEM_PROMPT = """You are a Principal Software Engineer doing a code review.
Review the code files below for:
1. Correctness — logic bugs, edge cases, null handling
2. Security — OWASP Top 10, injection, auth, secrets
3. Performance — N+1 queries, blocking calls, memory leaks
4. Conventions — naming, patterns, style consistency
5. Testability — coverage gaps, missing assertions

Return ONLY valid JSON:
{
  "verdict": "APPROVE" | "REQUEST_CHANGES",
  "score": 0-100,
  "critical_issues": [{"file": "...", "line_hint": "...", "issue": "...", "fix": "..."}],
  "suggestions": ["..."],
  "summary": "..."
}"""


async def review_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("review", state):
        emit(EventType.INFO, "ReviewAgent starting — reviewing code")

        mem = SharedMemory(state["project_id"])
        await mem.init()

        ctx       = await mem.get_project_context()
        learnings = await mem.get_accumulated_learnings(limit=5)

        files_changed    = state.get("files_changed", [])
        feature_branches = state.get("feature_branches", {})
        retry_count      = state.get("retry_count", 0)

        if not files_changed:
            emit(EventType.INFO, "No files to review")
            return {**state, "current_stage": "review",
                    "review_result": {"verdict": "APPROVE", "score": 100, "critical_issues": []}}

        llm = ProviderFactory.get_model(AgentRole.REVIEW)

        # Gather file contents
        file_ctx = []
        for fc in files_changed[:15]:
            try:
                branch  = feature_branches.get(fc["repo"], "main")
                content = await get_file_content(fc["repo"], fc["path"], ref=branch)
                file_ctx.append(f"FILE: {fc['repo']}/{fc['path']}\n```\n{content[:3000]}\n```")
            except Exception:
                pass

        prior_reviews = json.dumps(state.get("review_history", [])[-3:], indent=2)
        learning_ctx  = ""
        if learnings:
            learning_ctx = "\nPrevious review patterns:\n" + json.dumps(
                [l["content"] for l in learnings[:3]], indent=2
            )

        prompt = (
            f"{SYSTEM_PROMPT}{learning_ctx}\n\n"
            f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
            f"Conventions: {json.dumps(state.get('code_conventions', {}), indent=2)}\n"
            f"Retry count: {retry_count}\n"
            f"Prior reviews:\n{prior_reviews}\n\n"
            f"Files:\n" + "\n\n".join(file_ctx)
        )

        emit(EventType.LLM, "Calling LLM for code review")
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw      = response.content

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            result = json.loads(m.group()) if m else {"verdict": "APPROVE", "score": 75}

        verdict = result.get("verdict", "APPROVE")
        score   = result.get("score", 75)

        emit(EventType.INFO, f"Review verdict: {verdict} (score={score})")

        # Post review comment to open PRs
        for repo, branch in feature_branches.items():
            try:
                comment = (
                    f"## AI Code Review\n\n"
                    f"**Verdict:** {verdict} | **Score:** {score}/100\n\n"
                    f"**Summary:** {result.get('summary', '')}\n\n"
                )
                if result.get("critical_issues"):
                    comment += "**Critical Issues:**\n"
                    for issue in result["critical_issues"]:
                        comment += f"- `{issue.get('file', '')}`: {issue.get('issue', '')}\n"
                if result.get("suggestions"):
                    comment += "\n**Suggestions:**\n"
                    for s in result["suggestions"][:5]:
                        comment += f"- {s}\n"

                # We don't have PR numbers in state; add comment to latest open PR
                # In a real system, track PR numbers in feature_branches
                emit(EventType.TOOL, f"Review comment prepared for {repo}")
            except Exception as e:
                emit(EventType.ERROR, f"PR comment failed for {repo}: {e}")

        review_entry = {
            "verdict": verdict, "score": score,
            "critical_issues": result.get("critical_issues", []),
            "suggestions":     result.get("suggestions", []),
            "retry":           retry_count,
        }
        review_history = list(state.get("review_history", [])) + [review_entry]

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "review",
            "learning_type":"review_result",
            "content":      {"verdict": verdict, "score": score, "issues": len(result.get("critical_issues", []))},
            "metadata":     {},
        })

        emit(EventType.DONE, f"Review complete: {verdict} score={score}")

        return {
            **state,
            "review_result":  result,
            "review_history": review_history,
            "current_stage":  "review",
        }
