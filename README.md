# aisdlc-orchestrator

LangGraph-based AI orchestration engine that drives the full software development lifecycle — from a plain-English idea through requirements, design, implementation, testing, review, deployment, and E2E validation — with two human approval gates.

---

## Platform Context

```
┌──────────────────────────────────────────────────────────────────┐
│                        AI SDLC Platform                          │
│                                                                  │
│  ┌─────────────────┐   REST/WS   ┌──────────────────────────┐   │
│  │  aisdlc-frontend│────────────▶│  aisdlc-orchestrator     │   │
│  │  React 18 :3000 │◀────────────│  LangGraph + FastAPI     │   │
│  └─────────────────┘  events     │  ★ THIS REPO  :8001      │   │
│                                  └────────────┬─────────────┘   │
│                                               │ writes code      │
│                                  ┌────────────▼─────────────┐   │
│                                  │  aisdlc-backend          │   │
│                                  │  Spring Boot target :8080│   │
│                                  └──────────────────────────┘   │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  aisdlc-infra  —  Terraform + Helm  (EKS / ECS / AWS)    │  │
│  └───────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

External dependencies:
  Atlassian (Jira + Confluence)  ←  stories, TSD, Confluence pages
  GitHub                         ←  PR creation, branch management
  AWS Bedrock / Vertex AI        ←  LLM calls
  Redis 7                        ←  event streams, state checkpoints
  PostgreSQL 16 + pgvector       ←  RAG memory, pattern store
```

---

## Pipeline Flow

```
  idea
   │
   ▼
┌──────────────┐    Reads Confluence, builds project context,
│  confluence  │    seeds state with tech stack & conventions
└──────┬───────┘
       │
       ▼
┌──────────────┐    Generates Jira stories with acceptance criteria
│   stories    │    and story points; syncs to Jira project
└──────┬───────┘
       │
  ╔════▼════╗
  ║ PO GATE ║  ← human approval (API or UI)
  ╚════╤════╝    rejects raise ValueError and halt the run
       │
       ▼
┌──────────────┐    Writes Technical Design doc to Confluence;
│   design     │    produces architecture decisions + API contracts
└──────┬───────┘
       │
  ╔════▼══════╗
  ║ ARCH GATE ║  ← human approval (API or UI)
  ╚════╤══════╝
       │
       ▼
┌──────────────┐    Profile-aware code generation; writes to
│  implement   │    target repo branches (ImplementationAgent)
└──────┬───────┘◀──────────────┐
       │                       │ CHANGES_REQUESTED
       ▼                       │ (max 2 retries)
┌──────────────┐         ┌─────┴──────┐
│    test      │──FAIL──▶│  (retry)   │
└──────┬───────┘         └────────────┘
       │ PASS
       ▼
┌──────────────┐
│   review     │──CHANGES_REQUESTED──▶ implement (max 2 retries)
└──────┬───────┘
       │ APPROVE
       ▼
┌──────────────┐
│   deploy     │
└──────┬───────┘
       │
       ▼
┌──────────────┐    E2E strategy driven by repo profile
│     e2e      │    (playwright / detox / flutter / jest-only)
└──────────────┘
```

---

## Agents

| Agent | Node name | Responsibility |
|---|---|---|
| ConfluenceAgent | `confluence` | Reads project Confluence space; extracts tech stack, conventions, existing patterns into SDLCState |
| StoryAgent | `stories` | Breaks the idea into Jira stories with ACs and story points; syncs to Jira via REST |
| DesignAgent | `design` | Generates Technical System Design; publishes to Confluence under the SD space |
| ImplementationAgent | `implement` | Generates code per repo profile; writes files to feature branches in the target repo |
| TestAgent | `test` | Generates and runs unit/integration tests against the profile's test framework |
| ReviewAgent | `review` | Performs profile-aware code review using `review_rules`; verdict is APPROVE or CHANGES_REQUESTED |
| DeployAgent | `deploy` | Packages and triggers deployment; updates deploy_status in state |
| E2ETestAgent | `e2e` | Runs E2E suite per repo's `e2e_strategy` (Playwright / Detox / Flutter / Jest) |
| PO Gate | `po_gate` | LangGraph interrupt node — pipeline pauses here for PO sign-off |
| Arch Gate | `arch_gate` | LangGraph interrupt node — pipeline pauses here for architect sign-off |

---

## Tech Profile System

19 built-in profiles across 8 categories (Backend, Frontend, Serverless, Salesforce, Mobile, Streaming, Infrastructure, Data/ML), each carrying:

- `language`, `framework`, `test_framework`, `deploy_target`
- `conventions` — directory layout injected into ImplementationAgent prompt
- `review_rules` — stack-specific checklist injected into ReviewAgent prompt
- `e2e_strategy` — drives E2ETestAgent runner selection
- `mcp_servers` — per-stage MCP server list loaded at runtime

Profiles support single-level inheritance via `extends`. Child profiles declare only overrides; `resolve_profile()` merges parent → child at request time.

Custom profiles are stored in Redis (`profile:custom:{id}`) and served alongside built-ins by all `/api/profiles/*` endpoints.

---

## API Surface

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/pipeline/run` | Start a pipeline run; returns `execution_id` |
| `GET` | `/api/pipeline/{id}/status` | Poll run status |
| `GET` | `/api/pipeline/{id}/state` | Full SDLCState snapshot |
| `POST` | `/api/gate/{id}/approve` | Approve or reject a human gate |
| `WS` | `/ws/events/{id}` | Real-time event stream (Redis Streams fan-out) |
| `POST` | `/api/projects` | Register a team project config |
| `GET` | `/api/projects` | List all registered project configs |
| `GET` | `/api/projects/{id}` | Get a single project config |
| `DELETE` | `/api/projects/{id}` | Delete a project config |
| `GET` | `/api/profiles` | List all resolved profiles (built-in + custom) |
| `GET` | `/api/profiles/categories` | Profiles grouped by category |
| `GET` | `/api/profiles/{id}` | Single resolved profile |
| `POST` | `/api/profiles/custom` | Create a custom profile (persisted in Redis) |
| `GET` | `/api/profiles/custom` | List custom profiles |
| `DELETE` | `/api/profiles/custom/{id}` | Delete a custom profile |
| `GET` | `/api/validate/jira/{key}` | Validate a Jira project key |
| `GET` | `/api/validate/confluence/{key}` | Validate a Confluence space key |
| `POST` | `/api/validate/confluence/create-space` | Create a new Confluence space |
| `GET` | `/health` | Liveness check |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `REDIS_URL` | Yes | `redis://localhost:6379` (local); ElastiCache URL in AWS |
| `JIRA_BASE_URL` | Yes | e.g. `https://yourorg.atlassian.net` |
| `JIRA_EMAIL` | Yes | Atlassian account email for API auth |
| `JIRA_API_TOKEN` | Yes | Atlassian API token — generate at id.atlassian.com |
| `JIRA_CLOUD_ID` | Yes | Atlassian cloud ID (UUID) for your instance |
| `CONFLUENCE_BASE_URL` | Yes | e.g. `https://yourorg.atlassian.net/wiki` |
| `CONFLUENCE_EMAIL` | Yes | Same as `JIRA_EMAIL` unless separate |
| `CONFLUENCE_API_TOKEN` | Yes | Same as `JIRA_API_TOKEN` unless separate |
| `CONFLUENCE_SPACE_KEY` | Yes | Default Confluence space key (e.g. `SD`) |
| `CONFLUENCE_PARENT_PAGE` | Yes | Page ID to nest generated docs under |
| `GITHUB_TOKEN` | Yes | PAT with `repo` scope for branch/PR operations |
| `GITHUB_OWNER` | Yes | GitHub org or user name |
| `AWS_REGION` | No | Defaults to `us-east-1`; required for Bedrock |
| `ANTHROPIC_API_KEY` | No | Required if using Anthropic direct (not Bedrock) |
| `AGENT_MAX_RETRY` | No | Max implement retries on test/review failure (default `2`) |

Copy `.env.example` to `.env` and fill in values before first run.

---

## Local Setup

**Prerequisites:** Python 3.12+, Docker, Redis, PostgreSQL

```bash
# 1. Start infrastructure
docker compose up -d          # Redis :6380, PostgreSQL :5433

# 2. Create virtualenv and install deps
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with Atlassian, GitHub, and LLM credentials

# 4. Run schema migrations
psql postgresql://sdlc:sdlc@localhost:5433/sdlc_db -f schema.sql

# 5. Start the server
uvicorn sdlc_orchestrator.api.main:app --reload --port 8001
```

OpenAPI docs available at `http://localhost:8001/docs`.

---

## Redis Architecture

| Key pattern | Type | Purpose |
|---|---|---|
| `run:{id}:status` | String (JSON) | Current pipeline status and stage |
| `sdlc:events:{id}` | Stream | Per-run event log; WebSocket endpoint fans this out |
| `project:config:{id}` | String (JSON) | Registered team project config |
| `project:index` | Set | Index of all registered project IDs |
| `profile:custom:{id}` | String (JSON) | Custom tech profile |
| `profile:custom:index` | Set | Index of all custom profile IDs |

LangGraph state checkpoints are stored in-memory (`MemorySaver`) for local development. Swap for `RedisSaver` in production.

---

## Key Architectural Decisions

**Why LangGraph?** The pipeline requires stateful, interruptible graph execution with human-in-the-loop gates. LangGraph's `interrupt_before` mechanism pauses the graph at `po_gate` and `arch_gate` nodes; the gate approval endpoint resumes via `aupdate_state` + `astream(None, config)`.

**Why Redis Streams for events?** Multiple consumers (WebSocket, monitoring) need the same event sequence. Streams give fan-out, replay, and backpressure without a separate message broker.

**Why single-level profile inheritance?** Multi-level inheritance creates invisible coupling across profiles. One level (parent → child) is sufficient for the current taxonomy (e.g. `sfcc-pwakit` extends `salesforce-b2c-commerce`) and makes `resolve_profile()` predictable.

**Profile as pipeline config:** A profile is not just metadata — it drives which MCP servers load, which review checklist fires, and which E2E runner is invoked. Keeping this in a single object prevents config drift between pipeline stages.

---

## Confluence / Jira Reference

- Confluence space: `SD` (Software Development)
- Platform TSD: `https://bhaskarwork.atlassian.net/wiki/spaces/SD`
- Jira project for platform work: `AISDLC`
