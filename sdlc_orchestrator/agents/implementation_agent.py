"""sdlc_orchestrator/agents/implementation_agent.py
Generates code files across repos for each story, pushes branches, opens PRs.
"""
import re
import json
from langchain_core.messages import HumanMessage

from sdlc_orchestrator.state import SDLCState
from sdlc_orchestrator.memory.shared_memory import SharedMemory
from sdlc_orchestrator.providers.provider_factory import ProviderFactory, AgentRole
from sdlc_orchestrator.monitoring.tracker import EventType, emit, track_stage
from sdlc_orchestrator.tools.github_tools import (
    create_branch, upsert_file, create_pull_request,
)
from sdlc_orchestrator.tools.jira_tools import update_story_status

SYSTEM_PROMPT = """You are a Senior Full-Stack Engineer.
Implement the user story below following the conventions provided.
Output ONLY file blocks in this exact format (repeat for each file):

FILE: path/to/file.ext
```lang
<full file contents>
```

Cover all acceptance criteria. Include necessary imports. Do not truncate."""


def parse_file_blocks(content: str) -> list[tuple[str, str, str]]:
    pattern = r"FILE:\s*(.+?)\n```(\w+)?\n(.*?)```"
    matches = re.findall(pattern, content, re.DOTALL)
    return [(path.strip(), lang or "text", code.strip()) for path, lang, code in matches]


def resolve_repos(story: dict) -> list[str]:
    tags = story.get("tags", [])
    ac   = " ".join(story.get("acceptance_criteria", [])).lower()
    repos = []
    if any(t in tags for t in ["api", "backend"]) or "database" in ac:
        repos.append("aisdlc-backend")
    if any(t in tags for t in ["ui", "frontend"]) or any(w in ac for w in ["screen", "page", "form"]):
        repos.append("aisdlc-frontend")
    if any(t in tags for t in ["infra", "terraform"]) or "deploy" in ac:
        repos.append("aisdlc-infra")
    return repos or ["aisdlc-backend"]


async def implementation_agent_node(state: SDLCState) -> SDLCState:
    with track_stage("implement", state):
        emit(EventType.INFO, "ImplementationAgent starting")

        mem = SharedMemory(state["project_id"])
        await mem.init()

        ctx       = await mem.get_project_context()
        learnings = await mem.get_accumulated_learnings(limit=5)

        stories = state.get("stories", [])
        if not stories:
            raise ValueError("No stories available for implementation")

        llm          = ProviderFactory.get_model(AgentRole.IMPLEMENTATION)
        all_files    = []
        feature_branches: dict[str, str] = dict(state.get("feature_branches", {}))

        conventions = json.dumps(state.get("code_conventions", {}), indent=2)
        api_ctx     = json.dumps(state.get("api_contracts", [])[:5], indent=2)

        learning_ctx = ""
        if learnings:
            learning_ctx = "\nPrevious patterns:\n" + json.dumps(
                [l["content"] for l in learnings[:3]], indent=2
            )

        for story in stories:
            story_key  = story.get("jira_key", story["summary"][:20].replace(" ", "-"))
            repos      = resolve_repos(story)
            branch_name = f"feature/{story_key.lower()}-implementation"

            emit(EventType.INFO, f"Implementing [{story_key}]: {story['summary'][:60]}")

            prompt = (
                f"{SYSTEM_PROMPT}{learning_ctx}\n\n"
                f"Story: {story['summary']}\n"
                f"Description: {story.get('description', '')}\n"
                f"Acceptance Criteria:\n" + "\n".join(f"- {ac}" for ac in story.get("acceptance_criteria", [])) +
                f"\n\nTargets repos: {', '.join(repos)}\n"
                f"Tech stack: {', '.join(state.get('tech_stack', []))}\n"
                f"Conventions:\n{conventions}\n"
                f"API contracts:\n{api_ctx}"
            )

            emit(EventType.LLM, f"Calling LLM for [{story_key}]")
            response   = await llm.ainvoke([HumanMessage(content=prompt)])
            raw        = response.content
            file_blocks = parse_file_blocks(raw)

            emit(EventType.INFO, f"Parsed {len(file_blocks)} file(s) for [{story_key}]")

            for repo in repos:
                try:
                    await create_branch(repo, branch_name)
                    feature_branches[repo] = branch_name
                    emit(EventType.TOOL, f"Created branch {branch_name} in {repo}")
                except Exception as e:
                    emit(EventType.ERROR, f"Branch creation failed for {repo}: {e}")

            for path, lang, code in file_blocks:
                # Route file to correct repo based on path prefix
                target_repo = repos[0]
                if "frontend" in path or "src/" in path and "java" not in path:
                    target_repo = "aisdlc-frontend" if "aisdlc-frontend" in repos else repos[0]
                elif "java" in path or "src/main" in path:
                    target_repo = "aisdlc-backend" if "aisdlc-backend" in repos else repos[0]
                elif ".tf" in path or "helm" in path:
                    target_repo = "aisdlc-infra" if "aisdlc-infra" in repos else repos[0]

                try:
                    await upsert_file(
                        repo=target_repo,
                        path=path,
                        content=code,
                        branch=feature_branches.get(target_repo, branch_name),
                        message=f"feat({story_key}): {story['summary'][:72]}",
                    )
                    all_files.append({"repo": target_repo, "path": path, "story": story_key})
                    emit(EventType.TOOL, f"Pushed {path} → {target_repo}")
                except Exception as e:
                    emit(EventType.ERROR, f"File push failed {path}: {e}")

            # Open PRs
            for repo in repos:
                branch = feature_branches.get(repo, branch_name)
                try:
                    pr = await create_pull_request(
                        repo=repo,
                        title=f"[{story_key}] {story['summary'][:72]}",
                        body=f"Implements {story_key}\n\n{story.get('description', '')}",
                        head=branch,
                    )
                    emit(EventType.TOOL, f"Opened PR #{pr.get('number')} in {repo}")
                except Exception as e:
                    emit(EventType.ERROR, f"PR creation failed for {repo}: {e}")

            # Transition story to In Progress
            if story.get("jira_key"):
                try:
                    await update_story_status(story["jira_key"], "In Progress")
                except Exception:
                    pass

        await mem.save_story_learning({
            "story_id":     state.get("current_story_id", ""),
            "agent_name":   "implementation",
            "learning_type":"files_generated",
            "content":      {"file_count": len(all_files), "repos": list(feature_branches.keys())},
            "metadata":     {},
        })

        emit(EventType.DONE, f"Implementation complete: {len(all_files)} file(s) across {len(feature_branches)} repo(s)")

        return {
            **state,
            "files_changed":   all_files,
            "feature_branches": feature_branches,
            "current_stage":   "implement",
        }
