# -*- coding: utf-8 -*-
"""Additive schema for the unified knowledge store.

Design notes
------------
* ``libraries`` keeps its primary key, so every agent/team/workflow binding
  (``library_ids``) stays valid.
* ``documents`` keeps its primary key and gains the fields the chunk store
  needs (hash, counters, version, validity, parse mode).
* ``chunks`` is the single retrieval unit for both parse modes:
  ``page`` / ``section`` come from the document-tree pipeline, ``text`` /
  ``parent_text`` / ``image_*`` from the chunking pipeline.
* Nothing is dropped or renamed here; migration only adds.
"""
from .. import db

# Columns added to existing tables. (table, column, ddl)
LIBRARY_COLUMNS = (
    ("type", "TEXT DEFAULT 'document'"),
    ("config", "JSON DEFAULT '{}'"),
    ("default_validity_days", "INTEGER"),
    ("duplicate_scope", "TEXT DEFAULT 'current_kb'"),
    ("status", "TEXT DEFAULT 'active'"),
    ("creator_id", "TEXT DEFAULT ''"),
)

DOCUMENT_COLUMNS = (
    ("parse_mode", "TEXT DEFAULT 'chunk'"),      # tree | chunk
    ("parse_config", "JSON DEFAULT '{}'"),       # per-document snapshot
    ("file_hash", "TEXT DEFAULT ''"),
    ("file_size", "INTEGER DEFAULT 0"),
    ("chars", "INTEGER DEFAULT 0"),
    ("chunks_count", "INTEGER DEFAULT 0"),
    ("content_revision", "INTEGER DEFAULT 0"),
    ("active_version_id", "TEXT DEFAULT ''"),
    ("pending_version_id", "TEXT DEFAULT ''"),
    ("version_no", "INTEGER DEFAULT 0"),
    ("validity_mode", "TEXT DEFAULT 'inherit'"),
    ("valid_from", "TEXT DEFAULT ''"),
    ("expires_at", "TEXT DEFAULT ''"),
    ("enabled", "INTEGER DEFAULT 1"),
    ("folder_path", "TEXT DEFAULT ''"),
    ("channel", "TEXT DEFAULT 'web'"),
    ("tags", "JSON DEFAULT '[]'"),
    ("deleted_at", "TEXT"),
    ("summary_status", "TEXT DEFAULT 'none'"),
    ("questions_count", "INTEGER DEFAULT 0"),
    ("enriched_at", "TEXT DEFAULT ''"),
)

DDL = """
CREATE TABLE IF NOT EXISTS chunks(
  rid INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT UNIQUE,
  kb_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  chunk_index INTEGER DEFAULT 0,
  chunk_type TEXT DEFAULT 'text',
  content TEXT NOT NULL,
  source_content TEXT DEFAULT '',
  context_header TEXT DEFAULT '',
  heading TEXT DEFAULT '',
  page INTEGER DEFAULT 0,
  page_end INTEGER DEFAULT 0,
  start_at INTEGER DEFAULT 0,
  end_at INTEGER DEFAULT 0,
  parent_chunk_id TEXT DEFAULT '',
  pre_chunk_id TEXT DEFAULT '',
  next_chunk_id TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  flags INTEGER DEFAULT 1,
  index_status TEXT DEFAULT 'ready',
  content_revision INTEGER DEFAULT 0,
  content_hash TEXT DEFAULT '',
  image_info JSON DEFAULT '{}',
  metadata JSON DEFAULT '{}',
  last_editor_id TEXT DEFAULT '',
  created_at TEXT,
  updated_at TEXT);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  content, heading, context_header, tokenize='trigram');

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_chunks_kb ON chunks(kb_id, enabled);
CREATE INDEX IF NOT EXISTS idx_chunks_parent ON chunks(parent_chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(chunk_type);

CREATE TABLE IF NOT EXISTS document_versions(
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  parent_version_id TEXT DEFAULT '',
  rollback_from TEXT DEFAULT '',
  source TEXT DEFAULT 'parse',
  md_path TEXT NOT NULL,
  md_hash TEXT DEFAULT '',
  chars INTEGER DEFAULT 0,
  status TEXT DEFAULT 'creating',
  error TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at TEXT,
  activated_at TEXT);
CREATE INDEX IF NOT EXISTS idx_docver_doc ON document_versions(document_id);

CREATE TABLE IF NOT EXISTS chunk_revisions(
  id TEXT PRIMARY KEY,
  chunk_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  content TEXT,
  enabled INTEGER DEFAULT 1,
  editor_id TEXT DEFAULT '',
  edited_at TEXT,
  created_at TEXT,
  UNIQUE(chunk_id, revision));

CREATE TABLE IF NOT EXISTS kb_tags(
  id TEXT PRIMARY KEY,
  kb_id TEXT NOT NULL,
  name TEXT NOT NULL,
  color TEXT DEFAULT '',
  sort_order INTEGER DEFAULT 0,
  created_at TEXT,
  UNIQUE(kb_id, name));

CREATE TABLE IF NOT EXISTS document_tags(
  document_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  PRIMARY KEY(document_id, tag_id));

CREATE TABLE IF NOT EXISTS kb_folders(
  id TEXT PRIMARY KEY,
  kb_id TEXT NOT NULL,
  path TEXT NOT NULL,
  created_at TEXT,
  UNIQUE(kb_id, path));

CREATE TABLE IF NOT EXISTS recycle_entries(
  id TEXT PRIMARY KEY,
  kb_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  name TEXT DEFAULT '',
  deleted_by TEXT DEFAULT '',
  expires_at TEXT,
  created_at TEXT);

CREATE TABLE IF NOT EXISTS document_fingerprints(
  document_id TEXT PRIMARY KEY,
  normalized_hash TEXT,
  char_count INTEGER DEFAULT 0,
  minhash JSON DEFAULT '[]',
  shingles JSON DEFAULT '[]',
  algo_version TEXT DEFAULT 'v3',
  created_at TEXT);

CREATE TABLE IF NOT EXISTS duplicate_relations(
  id TEXT PRIMARY KEY,
  kb_id TEXT,
  document_id TEXT,
  other_id TEXT,
  kind TEXT DEFAULT 'text',
  coverage REAL DEFAULT 0,
  jaccard REAL DEFAULT 0,
  evidence JSON DEFAULT '{}',
  status TEXT DEFAULT 'open',
  created_at TEXT);

CREATE TABLE IF NOT EXISTS chunk_embeddings(
  rid INTEGER PRIMARY KEY,
  kb_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  model TEXT DEFAULT '',
  dim INTEGER DEFAULT 0,
  vector JSON DEFAULT '[]',
  created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_embed_kb ON chunk_embeddings(kb_id);

CREATE TABLE IF NOT EXISTS model_configs(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  provider TEXT DEFAULT 'openai',
  base_url TEXT DEFAULT '',
  api_key TEXT DEFAULT '',
  model_name TEXT DEFAULT '',
  dimension INTEGER DEFAULT 0,
  context_window INTEGER DEFAULT 0,
  supports_vision INTEGER DEFAULT 0,
  source TEXT DEFAULT 'custom',
  extra JSON DEFAULT '{}',
  enabled INTEGER DEFAULT 1,
  is_default INTEGER DEFAULT 0,
  created_at TEXT,
  updated_at TEXT);
CREATE INDEX IF NOT EXISTS idx_models_kind ON model_configs(kind, enabled);

CREATE TABLE IF NOT EXISTS sensitive_findings(
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  decision TEXT DEFAULT 'pass',
  level TEXT DEFAULT 'none',
  findings JSON DEFAULT '[]',
  signature TEXT DEFAULT '',
  status TEXT DEFAULT 'pending',
  created_at TEXT);
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(conn, table, columns):
    existing = _columns(conn, table)
    for name, ddl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init():
    conn = db.conn()
    conn.executescript(DDL)
    _add_columns(conn, "libraries", LIBRARY_COLUMNS)
    _add_columns(conn, "documents", DOCUMENT_COLUMNS)
    _add_columns(conn, "model_configs", (
        ("dimension", "INTEGER DEFAULT 0"),
        ("context_window", "INTEGER DEFAULT 0"),
        ("supports_vision", "INTEGER DEFAULT 0"),
        ("source", "TEXT DEFAULT 'custom'"),
    ))
    conn.execute("UPDATE model_configs SET kind='chat' WHERE kind='llm'")
    conn.execute("UPDATE model_configs SET kind='vllm' WHERE kind='vlm'")
    conn.commit()
    return conn