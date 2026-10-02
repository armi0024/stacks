-- Phase 1a migration: ALL phase-1 schema ships here (SPEC P13).
-- 1b adds behavior, not tables. Idempotent by convention (see core/db.py).
-- Sensitivity order (ranked in code): open < internal < confidential < restricted.

-- ============================================================ identity layers

CREATE TABLE IF NOT EXISTS documents (
  id               TEXT PRIMARY KEY,
  title            TEXT NOT NULL,
  issuer           TEXT,
  language         TEXT,                        -- identity language field
  identity_numbers TEXT NOT NULL DEFAULT '{}',  -- JSON {kind: value}
  metadata         TEXT NOT NULL DEFAULT '{}',  -- JSON; ext.* namespaced keys live here
  doc_type         TEXT,
  schematic_implied INTEGER NOT NULL DEFAULT 0, -- operator-asserted
  primary_domain   TEXT NOT NULL,
  additional_domains TEXT NOT NULL DEFAULT '[]',-- JSON array (P5: domain is a set)
  sensitivity      TEXT NOT NULL CHECK (sensitivity IN ('open','internal','confidential','restricted')),
  legal_hold       INTEGER NOT NULL DEFAULT 0,
  effective_date   TEXT,                        -- bitemporal: content speaks as of
  recorded_at      TEXT NOT NULL,               -- bitemporal: when Stacks learned of it
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  row_version      INTEGER NOT NULL DEFAULT 1   -- mutation contract: expected-prior-version
);

CREATE TABLE IF NOT EXISTS document_applicability (
  document_id TEXT NOT NULL REFERENCES documents(id),
  kind        TEXT NOT NULL CHECK (kind IN ('game','entity')),
  ref         TEXT NOT NULL,                    -- mame_name or entity name
  PRIMARY KEY (document_id, kind, ref)
);

CREATE TABLE IF NOT EXISTS revisions (
  id             TEXT PRIMARY KEY,
  document_id    TEXT NOT NULL REFERENCES documents(id),
  label          TEXT,                          -- 'Rev A', amendment name
  ordinal        INTEGER,                       -- ordering within the document
  effective_date TEXT,
  recorded_at    TEXT NOT NULL,
  is_current     INTEGER NOT NULL DEFAULT 0     -- workflow pointer (phase 7 active)
);
CREATE INDEX IF NOT EXISTS idx_revisions_document ON revisions(document_id);

CREATE TABLE IF NOT EXISTS copies (
  id                 TEXT PRIMARY KEY,
  revision_id        TEXT NOT NULL REFERENCES revisions(id),
  source_class       TEXT NOT NULL,             -- my_scan | ia | tama | open_dir | ingest_folder | ...
  format             TEXT,
  page_count         INTEGER,
  sha256             TEXT NOT NULL,
  md5                TEXT,                      -- retained where published (IA)
  superseded         INTEGER NOT NULL DEFAULT 0,
  -- primary state facts (6.3); evidence lives in analysis_runs
  assessment_mode    TEXT CHECK (assessment_mode IN ('screening','assess') OR assessment_mode IS NULL),
  analysis_config_hash TEXT,
  completeness_status  TEXT CHECK (completeness_status IN ('confirmed','known-incomplete','unknown') OR completeness_status IS NULL),
  missing_fraction   REAL,
  if_text REAL, tq_text REAL, if_schem REAL, tq_schem REAL,
  image_adequate     INTEGER,                   -- null = unknown
  text_adequate      INTEGER,
  fitness_text REAL, fitness_schem REAL,        -- ranking only, never adequacy
  pages_below_adequacy TEXT NOT NULL DEFAULT '{}', -- JSON {image: n, text: n}
  risk_flags         TEXT NOT NULL DEFAULT '[]',   -- JSON; inherited flags propagate
  jbig2_acknowledged INTEGER NOT NULL DEFAULT 0,   -- operator ack gates CONFIRMED
  recommended_action TEXT,
  action_labels      TEXT NOT NULL DEFAULT '[]',
  -- canonical assignment (6.4): one document_default copy per document
  is_document_default INTEGER NOT NULL DEFAULT 0,
  default_state      TEXT CHECK (default_state IN ('provisional','confirmed') OR default_state IS NULL),
  default_actor      TEXT,                      -- system rule for provisional; principal for confirmed
  default_at         TEXT,
  default_content_hash TEXT,
  default_supersedes TEXT,
  provenance         TEXT NOT NULL DEFAULT '{}',
  created_at         TEXT NOT NULL,
  row_version        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_copies_revision ON copies(revision_id);
CREATE INDEX IF NOT EXISTS idx_copies_sha256 ON copies(sha256);

CREATE TABLE IF NOT EXISTS file_assets (
  id            TEXT PRIMARY KEY,
  sha256        TEXT NOT NULL UNIQUE,           -- the dedup registry (4.1)
  md5           TEXT,
  size_bytes    INTEGER NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('intake','raw','master','access','ocr','cache','backup')),
  rel_path      TEXT NOT NULL,                  -- under files_root; carries NO organizational meaning
  media_type    TEXT,
  parent_asset_id TEXT REFERENCES file_assets(id),  -- lineage: raw -> master -> access
  producer_tool TEXT,
  producer_version TEXT,
  source_urls   TEXT NOT NULL DEFAULT '[]',     -- JSON; multiple per asset
  created_at    TEXT NOT NULL
);
-- No deletion of stored evidence, ever (SPEC non-goals); GC of rebuildable
-- caches happens outside this table.
CREATE TRIGGER IF NOT EXISTS file_assets_no_delete
BEFORE DELETE ON file_assets BEGIN
  SELECT RAISE(ABORT, 'file_assets are immutable: deletion forbidden');
END;
CREATE TRIGGER IF NOT EXISTS copies_no_delete
BEFORE DELETE ON copies BEGIN
  SELECT RAISE(ABORT, 'copies are never deleted: supersede instead');
END;
CREATE TRIGGER IF NOT EXISTS documents_no_delete_on_hold
BEFORE DELETE ON documents WHEN OLD.legal_hold = 1 BEGIN
  SELECT RAISE(ABORT, 'legal hold blocks destruction');
END;

CREATE TABLE IF NOT EXISTS copy_assets (
  copy_id  TEXT NOT NULL REFERENCES copies(id),
  asset_id TEXT NOT NULL REFERENCES file_assets(id),
  relation TEXT NOT NULL CHECK (relation IN ('primary','raw','master','access','ocr','derived')),
  PRIMARY KEY (copy_id, asset_id, relation)
);

CREATE TABLE IF NOT EXISTS pages (
  id             TEXT PRIMARY KEY,
  copy_id        TEXT NOT NULL REFERENCES copies(id),
  pdf_index      INTEGER NOT NULL,              -- 0-based position in the file
  printed_label  TEXT,                          -- what the page says it is
  sheet_id       TEXT,                          -- schematic sheet identity
  partial_capture INTEGER NOT NULL DEFAULT 0,   -- fold-out half etc.
  page_type      TEXT,                          -- native-text | ocr-layer | image-only | mixed
  content_class  TEXT,                          -- blank | text | schematic | mixed
  if_score REAL, tq_score REAL,
  tq_null_reason TEXT,
  text_origin    TEXT,                          -- verbatim-ocr | native-text (9.2 labels)
  extracted_text TEXT,                          -- indexed via pages_fts
  UNIQUE (copy_id, pdf_index)
);
CREATE INDEX IF NOT EXISTS idx_pages_copy ON pages(copy_id);

CREATE TABLE IF NOT EXISTS page_alignments (
  id           TEXT PRIMARY KEY,
  from_page_id TEXT NOT NULL REFERENCES pages(id),
  to_page_id   TEXT NOT NULL REFERENCES pages(id),
  method       TEXT NOT NULL,                   -- labels | visual | operator
  status       TEXT NOT NULL CHECK (status IN ('proposed','confirmed')),
  actor        TEXT,
  created_at   TEXT NOT NULL,
  UNIQUE (from_page_id, to_page_id)
);

-- ===================================================== evidence and operations

CREATE TABLE IF NOT EXISTS analysis_runs (
  id          TEXT PRIMARY KEY,
  copy_id     TEXT NOT NULL REFERENCES copies(id),
  config_hash TEXT NOT NULL,                    -- code + settings + weights (v1.5 ch.12)
  mode        TEXT NOT NULL CHECK (mode IN ('screening','assess')),
  coverage    TEXT NOT NULL,                    -- JSON: sampled indices, counts
  report      TEXT NOT NULL,                    -- full analyzer JSON report
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_copy ON analysis_runs(copy_id);

CREATE TABLE IF NOT EXISTS physical_holdings (
  id            TEXT PRIMARY KEY,
  document_id   TEXT NOT NULL REFERENCES documents(id),
  revision_id   TEXT REFERENCES revisions(id),
  condition     TEXT,
  missing_sheets TEXT NOT NULL DEFAULT '[]',
  shelf         TEXT,
  notes         TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS acquisition_coverage (
  id         TEXT PRIMARY KEY,
  source     TEXT NOT NULL,
  subject    TEXT NOT NULL,                     -- document identity or category checked
  checked_at TEXT NOT NULL,
  outcome    TEXT NOT NULL CHECK (outcome IN ('found','not-found','failed','unexamined')),
  detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_coverage_source ON acquisition_coverage(source, subject);

CREATE TABLE IF NOT EXISTS access_events (
  id          TEXT PRIMARY KEY,
  actor       TEXT NOT NULL,
  token_id    TEXT,
  verb        TEXT NOT NULL,
  object_kind TEXT NOT NULL,
  object_id   TEXT NOT NULL,
  at          TEXT NOT NULL,
  detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_access_events_object ON access_events(object_kind, object_id);

CREATE TABLE IF NOT EXISTS jobs (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  payload     TEXT NOT NULL DEFAULT '{}',
  status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed','dead')),
  priority    INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT UNIQUE,
  lease_until TEXT,
  worker_id   TEXT,
  heartbeat_at TEXT,
  attempts    INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 5,
  result      TEXT,
  error       TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, priority, created_at);

CREATE TABLE IF NOT EXISTS outbox (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  applied_at TEXT,
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS sources (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  class      TEXT NOT NULL,                     -- provenance/source class, data not code
  config     TEXT NOT NULL DEFAULT '{}',
  politeness TEXT NOT NULL DEFAULT '{}',
  writer_principal TEXT                          -- per-class writer restriction (3.6)
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL                            -- JSON
);

CREATE TABLE IF NOT EXISTS storage_projections (
  id             TEXT PRIMARY KEY,
  session        TEXT NOT NULL,
  recorded_at    TEXT NOT NULL,
  bytes_by_class TEXT NOT NULL                   -- JSON {artifact class: bytes}
);

CREATE TABLE IF NOT EXISTS phi_refusals (
  id          TEXT PRIMARY KEY,
  path        TEXT NOT NULL,                     -- path + reason code, never content
  reason_code TEXT NOT NULL,
  at          TEXT NOT NULL
);

-- ================================================== organization and autonomy

CREATE TABLE IF NOT EXISTS subjects (
  id        TEXT PRIMARY KEY,
  parent_id TEXT REFERENCES subjects(id),
  name      TEXT NOT NULL,
  UNIQUE (parent_id, name)
);

CREATE TABLE IF NOT EXISTS document_subjects (
  document_id TEXT NOT NULL REFERENCES documents(id),
  subject_id  TEXT NOT NULL REFERENCES subjects(id),
  provenance  TEXT NOT NULL CHECK (provenance IN ('human','auto')),
  decision_id TEXT,
  PRIMARY KEY (document_id, subject_id)
);

CREATE TABLE IF NOT EXISTS group_decisions (
  id         TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,                      -- auto-confirm rule applied
  object_ids TEXT NOT NULL,                      -- JSON
  rule       TEXT NOT NULL,
  actor      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  reverted_at TEXT
);

CREATE TABLE IF NOT EXISTS tag_decisions (
  id          TEXT PRIMARY KEY,
  subject_id  TEXT NOT NULL REFERENCES subjects(id),
  document_id TEXT NOT NULL REFERENCES documents(id),
  proposed_by TEXT NOT NULL,
  decision    TEXT NOT NULL CHECK (decision IN ('pending','accepted','rejected')),
  reviewed_by TEXT,
  created_at  TEXT NOT NULL,
  reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS subject_trust (
  subject_id        TEXT PRIMARY KEY REFERENCES subjects(id),
  precision_correct INTEGER NOT NULL DEFAULT 0,
  precision_total   INTEGER NOT NULL DEFAULT 0,
  recall_correct    INTEGER NOT NULL DEFAULT 0,
  recall_total      INTEGER NOT NULL DEFAULT 0,
  status            TEXT NOT NULL DEFAULT 'REVIEW',
  updated_at        TEXT NOT NULL
);

-- ================================================ authority and derivations

CREATE TABLE IF NOT EXISTS authority_records (
  id             TEXT PRIMARY KEY,
  principal      TEXT NOT NULL,                  -- authenticated writer
  object_kind    TEXT NOT NULL CHECK (object_kind IN ('document','revision','copy')),
  object_id      TEXT NOT NULL,
  status         TEXT NOT NULL,                  -- approved-for-use, retired, ...
  scope          TEXT,
  effective_date TEXT,
  recorded_at    TEXT NOT NULL,
  supersedes     TEXT
);
CREATE INDEX IF NOT EXISTS idx_authority_object ON authority_records(object_kind, object_id);
-- Append-only: one authoritative writer per record is enforced here or nowhere (4.4).
CREATE TRIGGER IF NOT EXISTS authority_no_update
BEFORE UPDATE ON authority_records BEGIN
  SELECT RAISE(ABORT, 'authority_records are append-only');
END;
CREATE TRIGGER IF NOT EXISTS authority_no_delete
BEFORE DELETE ON authority_records BEGIN
  SELECT RAISE(ABORT, 'authority_records are append-only');
END;

CREATE TABLE IF NOT EXISTS derivations (
  id              TEXT PRIMARY KEY,
  document_id     TEXT NOT NULL REFERENCES documents(id),  -- the derived document
  depends_on_kind TEXT NOT NULL CHECK (depends_on_kind IN ('document','revision','copy','citation')),
  depends_on_id   TEXT NOT NULL,
  citation        TEXT,                          -- JSON locator when citation-level
  stale           INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_derivations_dep ON derivations(depends_on_kind, depends_on_id);

-- ======================================================= principals and tokens

CREATE TABLE IF NOT EXISTS principals (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  kind       TEXT NOT NULL CHECK (kind IN ('owner','session','agent','service')),
  grants     TEXT NOT NULL DEFAULT '{}',         -- JSON {domain: max sensitivity}
  verbs      TEXT NOT NULL DEFAULT '[]',         -- JSON array
  active     INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
  id           TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL REFERENCES principals(id),
  issued_at    TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  revoked_at   TEXT
);

-- ================================================================ search (FTS)

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
  extracted_text,
  content='pages',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2 tokenchars ''-/.'''
);
CREATE TRIGGER IF NOT EXISTS pages_fts_insert AFTER INSERT ON pages BEGIN
  INSERT INTO pages_fts(rowid, extracted_text) VALUES (NEW.rowid, COALESCE(NEW.extracted_text, ''));
END;
CREATE TRIGGER IF NOT EXISTS pages_fts_delete AFTER DELETE ON pages BEGIN
  INSERT INTO pages_fts(pages_fts, rowid, extracted_text) VALUES ('delete', OLD.rowid, COALESCE(OLD.extracted_text, ''));
END;
CREATE TRIGGER IF NOT EXISTS pages_fts_update AFTER UPDATE OF extracted_text ON pages BEGIN
  INSERT INTO pages_fts(pages_fts, rowid, extracted_text) VALUES ('delete', OLD.rowid, COALESCE(OLD.extracted_text, ''));
  INSERT INTO pages_fts(rowid, extracted_text) VALUES (NEW.rowid, COALESCE(NEW.extracted_text, ''));
END;
