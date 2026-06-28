"""sdlc_orchestrator/memory/shared_memory.py
Dual-backend memory: Redis (project context, 30d TTL) + PostgreSQL (story learnings, permanent).
pgvector used for semantic pattern search over code patterns.
"""
import os
import json
import asyncio
from typing import Any, Optional
from datetime import datetime, timezone

import asyncpg
import redis.asyncio as aioredis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
PG_DSN    = os.environ.get("DATABASE_URL", "postgresql://sdlc:sdlc@localhost:5432/sdlc_db")
CTX_TTL   = 30 * 24 * 3600  # 30 days


class SharedMemory:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self._redis: Optional[aioredis.Redis] = None
        self._pg:    Optional[asyncpg.Connection] = None

    async def init(self):
        self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        self._pg    = await asyncpg.connect(PG_DSN)

    async def close(self):
        if self._redis:
            await self._redis.aclose()
        if self._pg:
            await self._pg.close()

    # ─── Project Context (Redis) ───────────────────────────────────────────────

    async def get_project_context(self) -> dict:
        key  = f"project:{self.project_id}:context"
        raw  = await self._redis.get(key)
        return json.loads(raw) if raw else {}

    async def set_project_context(self, ctx: dict):
        key = f"project:{self.project_id}:context"
        await self._redis.set(key, json.dumps(ctx), ex=CTX_TTL)

    async def update_project_context(self, updates: dict):
        ctx = await self.get_project_context()
        ctx.update(updates)
        await self.set_project_context(ctx)

    # ─── Story Learnings (PostgreSQL) ─────────────────────────────────────────

    async def save_story_learning(self, learning: dict):
        await self._pg.execute(
            """
            INSERT INTO story_learnings
                (project_id, story_id, agent_name, learning_type, content, metadata, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            self.project_id,
            learning.get("story_id", ""),
            learning.get("agent_name", ""),
            learning.get("learning_type", "general"),
            json.dumps(learning.get("content", {})),
            json.dumps(learning.get("metadata", {})),
            datetime.now(timezone.utc),
        )

    async def get_accumulated_learnings(self, limit: int = 5) -> list[dict]:
        rows = await self._pg.fetch(
            """
            SELECT story_id, agent_name, learning_type, content, metadata, created_at
            FROM story_learnings
            WHERE project_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            self.project_id,
            limit,
        )
        return [
            {
                "story_id":     r["story_id"],
                "agent_name":   r["agent_name"],
                "learning_type":r["learning_type"],
                "content":      json.loads(r["content"]),
                "metadata":     json.loads(r["metadata"]),
                "created_at":   r["created_at"].isoformat(),
            }
            for r in rows
        ]

    # ─── Pattern Embeddings (pgvector) ────────────────────────────────────────

    async def store_pattern_embedding(self, pattern_id: str, embedding: list[float], metadata: dict):
        await self._pg.execute(
            """
            INSERT INTO pattern_embeddings (project_id, pattern_id, embedding, metadata, created_at)
            VALUES ($1, $2, $3::vector, $4, $5)
            ON CONFLICT (project_id, pattern_id) DO UPDATE
              SET embedding = EXCLUDED.embedding,
                  metadata  = EXCLUDED.metadata,
                  created_at = EXCLUDED.created_at
            """,
            self.project_id,
            pattern_id,
            str(embedding),
            json.dumps(metadata),
            datetime.now(timezone.utc),
        )

    async def search_similar_patterns(self, embedding: list[float], limit: int = 5) -> list[dict]:
        rows = await self._pg.fetch(
            """
            SELECT pattern_id, metadata,
                   embedding <-> $2::vector AS distance
            FROM pattern_embeddings
            WHERE project_id = $1
            ORDER BY embedding <-> $2::vector
            LIMIT $3
            """,
            self.project_id,
            str(embedding),
            limit,
        )
        return [
            {
                "pattern_id": r["pattern_id"],
                "metadata":   json.loads(r["metadata"]),
                "distance":   float(r["distance"]),
            }
            for r in rows
        ]

    # ─── Redis Streams (event forwarding) ─────────────────────────────────────

    async def publish_event(self, execution_id: str, event: dict):
        key = f"sdlc:events:{execution_id}"
        await self._redis.xadd(key, {"data": json.dumps(event)}, maxlen=1000)

    async def set_run_status(self, execution_id: str, status: dict):
        key = f"run:{execution_id}:status"
        await self._redis.set(key, json.dumps(status), ex=3600)

    async def get_run_status(self, execution_id: str) -> Optional[dict]:
        raw = await self._redis.get(f"run:{execution_id}:status")
        return json.loads(raw) if raw else None
