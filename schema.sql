CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS story_learnings (
  id BIGSERIAL PRIMARY KEY,
  project_id TEXT NOT NULL,
  story_id TEXT NOT NULL UNIQUE,
  patterns_used JSONB DEFAULT '[]',
  files_changed JSONB DEFAULT '[]',
  test_coverage JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pattern_embeddings (
  id BIGSERIAL PRIMARY KEY,
  project_id TEXT NOT NULL,
  pattern_text TEXT NOT NULL,
  embedding vector(1536),
  UNIQUE(project_id, pattern_text)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id BIGSERIAL PRIMARY KEY,
  execution_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW()
);