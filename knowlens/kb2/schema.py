# -*- coding: utf-8 -*-
"""Additive schema for the chunk-level knowledge base."""
from .. import db

DDL = """
CREATE TABLE IF NOT EXISTS kb2_bases(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
  chunk_size INTEGER DEFAULT 512, chunk_overlap INTEGER DEFAULT 80,
  embedding_model TEXT DEFAULT '', status TEXT DEFAULT 'ready',
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS kb2_knowledges(
  id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, title TEXT NOT NULL,
  kind TEXT DEFAULT 'file', source TEXT DEFAULT '', storage_path TEXT DEFAULT '',
  status TEXT DEFAULT 'pending', error TEXT DEFAULT '', chars INTEGER DEFAULT 0,
  chunks_count INTEGER DEFAULT 0, pages INTEGER DEFAULT 0,
  sha TEXT DEFAULT '', tags JSON DEFAULT '[]', enabled INTEGER DEFAULT 1,
  valid_from TEXT DEFAULT '', valid_to TEXT DEFAULT '',
  version INTEGER DEFAULT 1,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS kb2_doc_versions(
  id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL, version INTEGER DEFAULT 1,
  sha TEXT DEFAULT '', storage_path TEXT DEFAULT '', chars INTEGER DEFAULT 0,
  chunks_count INTEGER DEFAULT 0, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_kb2_ver_doc ON kb2_doc_versions(knowledge_id);
CREATE TABLE IF NOT EXISTS kb2_chunks(
  rid INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, kb_id TEXT NOT NULL,
  knowledge_id TEXT NOT NULL, idx INTEGER DEFAULT 0, content TEXT NOT NULL,
  heading TEXT DEFAULT '', page INTEGER DEFAULT 0, start_at INTEGER DEFAULT 0,
  end_at INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1, created_at TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS kb2_chunks_fts USING fts5(
  content, heading, tokenize='trigram');
CREATE INDEX IF NOT EXISTS idx_kb2_docs_base ON kb2_knowledges(kb_id);
CREATE INDEX IF NOT EXISTS idx_kb2_chunks_doc ON kb2_chunks(knowledge_id);
CREATE INDEX IF NOT EXISTS idx_kb2_chunks_base ON kb2_chunks(kb_id);
"""


def init():
    c = db.conn()
    c.executescript(DDL)
    cols = {r[1] for r in c.execute("PRAGMA table_info(kb2_knowledges)")}
    for name, ddl in (("sha", "TEXT DEFAULT ''"),
                      ("tags", "JSON DEFAULT '[]'"),
                      ("enabled", "INTEGER DEFAULT 1"),
                      ("valid_from", "TEXT DEFAULT ''"),
                      ("valid_to", "TEXT DEFAULT ''"),
                      ("version", "INTEGER DEFAULT 1")):
        if name not in cols:
            c.execute(f"ALTER TABLE kb2_knowledges ADD COLUMN {name} {ddl}")
    c.commit()