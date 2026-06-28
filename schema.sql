CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS story_learnings (
  id           BIGSERIAL PRIMARY KEY,
  project_id   TEXT NOT NULL,
  story_id     TEXT NOT NULL DEFAULT '',
  agent_name   TEXT NOT NULL DEFAULT '',
  learning_type TEXT NOT NULL DEFAULT 'general',
  content      JSONB NOT NULL DEFAULT '{}',
  metadata     JSONB NOT NULL DEFAULT '{}',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_story_learnings_project
  ON story_learnings (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS pattern_embeddings (
  id          BIGSERIAL PRIMARY KEY,
  project_id  TEXT NOT NULL,
  pattern_id  TEXT NOT NULL,
  embedding   vector(1536),
  metadata    JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (project_id, pattern_id)
);

CREATE INDEX IF NOT EXISTS idx_pattern_embeddings_project
  ON pattern_embeddings (project_id);

CREATE TABLE IF NOT EXISTS audit_log (
  id           BIGSERIAL PRIMARY KEY,
  execution_id TEXT NOT NULL,
  event_type   TEXT NOT NULL,
  payload      JSONB NOT NULL DEFAULT '{}',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_execution
  ON audit_log (execution_id, created_at DESC);
