# aisdlc-orchestrator

AI SDLC Orchestration Platform - LangGraph engine with 9 specialized agents.

## Stack
- Python 3.12, LangGraph 0.2, FastAPI
- AWS Bedrock (Claude 3.5 Sonnet, Haiku, Llama3)
- Google Vertex AI (Gemini 1.5 Pro, Flash, Vision)
- Redis 7, PostgreSQL 16 + pgvector

## Quick Start
```bash
cp .env.example .env
docker compose up -d
pip install -r requirements.txt
uvicorn sdlc_orchestrator.api.main:app --reload --port 8000
```

## Docs
https://bhaskarwork.atlassian.net/wiki/spaces/SD/pages/50200578