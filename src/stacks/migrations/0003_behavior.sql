-- Phase 1b migration (SPEC 3.5 / P10): additive only, idempotent.
-- P13 intended all schema in 1a; the mutation-idempotency ledger, the
-- per-document generation pointer, and the generation-versioned chunk store
-- were missed there. Shipping them additively both fills the gap and
-- exercises the migration tooling, which 1b requires (deviation recorded in
-- DECISIONS.md).

-- Mutation contract (3.5): replayed idempotency keys return the original
-- result; this ledger is that memory. Append-only in practice (no updates).
CREATE TABLE IF NOT EXISTS mutations (
  id              TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  actor           TEXT NOT NULL,
  token_id        TEXT,
  operation       TEXT NOT NULL,
  object_kind     TEXT NOT NULL,
  object_id       TEXT NOT NULL,
  new_version     INTEGER,
  result          TEXT NOT NULL DEFAULT '{}',   -- JSON, returned verbatim on replay
  created_at      TEXT NOT NULL
);

-- Generation activation (3.5): one pointer per document; workers carry the
-- generation they serve and are rejected here when stale.
CREATE TABLE IF NOT EXISTS document_generations (
  document_id       TEXT PRIMARY KEY REFERENCES documents(id),
  active_generation INTEGER NOT NULL DEFAULT 0, -- 0 = never indexed
  updated_at        TEXT NOT NULL
);

-- Generation-versioned chunk store (9.2): the default representation of
-- EVERY revision is indexed; revision identity travels in chunk metadata.
-- Chunks are rebuildable derivatives: GC of inactive generations is allowed.
CREATE TABLE IF NOT EXISTS chunks (
  id          TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id),
  revision_id TEXT NOT NULL REFERENCES revisions(id),
  copy_id     TEXT NOT NULL REFERENCES copies(id),
  page_id     TEXT NOT NULL REFERENCES pages(id),
  page_number INTEGER NOT NULL,                 -- 1-based pdf position
  generation  INTEGER NOT NULL,
  seq         INTEGER NOT NULL,                 -- chunk index within the page
  text        TEXT NOT NULL,
  metadata    TEXT NOT NULL DEFAULT '{}'        -- JSON (9.2 chunk metadata)
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_gen ON chunks(document_id, generation);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text,
  content='chunks',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2 tokenchars ''-/.'''
);
CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (NEW.rowid, NEW.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', OLD.rowid, OLD.text);
END;
