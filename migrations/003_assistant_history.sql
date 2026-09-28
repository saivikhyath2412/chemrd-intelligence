-- Persistent, per-account Research Assistant question history.
CREATE TABLE IF NOT EXISTS assistant_history (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  citations JSONB NOT NULL DEFAULT '[]'::jsonb,
  assistant_status TEXT NOT NULL DEFAULT '',
  assistant_provider TEXT,
  memory_status TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS assistant_history_user_idx ON assistant_history(user_id, created_at DESC);
