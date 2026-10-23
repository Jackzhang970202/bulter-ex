# -*- coding: utf-8 -*-
"""SQLite storage layer: WAL + thread-local connections (pattern modeled on butler/db.py)."""
import os
import sqlite3
import threading
import time
import uuid

from . import config

_local = threading.local()


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        os.makedirs(config.DATA, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
    return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS spaces(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
  created_at TEXT);
CREATE TABLE IF NOT EXISTS libraries(
  id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
  name TEXT NOT NULL, description TEXT DEFAULT '', summary TEXT DEFAULT '',
  created_at TEXT);
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, space_id TEXT NOT NULL, library_id TEXT NOT NULL
    REFERENCES libraries(id) ON DELETE CASCADE,
  name TEXT NOT NULL, kind TEXT, doc_id TEXT, status TEXT DEFAULT 'pending',
  pages INTEGER DEFAULT 0, description TEXT DEFAULT '', summary TEXT DEFAULT '',
  source_path TEXT, error TEXT DEFAULT '',
  image_count INTEGER DEFAULT 0, vision INTEGER DEFAULT 1,
  ocr_status TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS doc_images(
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page INTEGER DEFAULT 0, order_no INTEGER DEFAULT 0,
  img_type TEXT DEFAULT '', transcription TEXT DEFAULT '', description TEXT DEFAULT '',
  status TEXT DEFAULT 'ok', sha TEXT DEFAULT '', img_path TEXT DEFAULT '',
  created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_img_doc ON doc_images(document_id);
CREATE TABLE IF NOT EXISTS tags(
  id TEXT PRIMARY KEY, space_id TEXT NOT NULL, name TEXT NOT NULL,
  auto INTEGER DEFAULT 0, created_at TEXT, UNIQUE(space_id, name));
CREATE TABLE IF NOT EXISTS doc_tags(
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(document_id, tag_id));
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, space_id TEXT NOT NULL, mode TEXT NOT NULL,
  title TEXT DEFAULT '', context JSON DEFAULT '[]', scope JSON DEFAULT '{}',
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT, content TEXT, meta JSON DEFAULT '{}', created_at TEXT);
CREATE TABLE IF NOT EXISTS qa_events(
  id TEXT PRIMARY KEY, session_id TEXT, space_id TEXT, question TEXT,
  answer TEXT, verdict TEXT, citations JSON DEFAULT '[]',
  feedback TEXT DEFAULT '', correction TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS candidates(
  id TEXT PRIMARY KEY, space_id TEXT NOT NULL, kind TEXT NOT NULL,
  statement TEXT, ex_bad TEXT DEFAULT '', ex_good TEXT DEFAULT '',
  evidence JSON DEFAULT '[]', source TEXT DEFAULT '',
  status TEXT DEFAULT 'pending', dedup_key TEXT DEFAULT '',
  times_seen INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT, space_id TEXT NOT NULL,
  statement TEXT NOT NULL, ex_bad TEXT DEFAULT '', ex_good TEXT DEFAULT '',
  status TEXT DEFAULT 'active', source TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS profiles(
  id TEXT PRIMARY KEY, space_id TEXT NOT NULL, name TEXT NOT NULL,
  content TEXT, ttl_days INTEGER DEFAULT 90, updated_at TEXT);
CREATE TABLE IF NOT EXISTS digests(
  id TEXT PRIMARY KEY, space_id TEXT NOT NULL, ver INTEGER NOT NULL,
  bytes INTEGER, nonce TEXT, content TEXT, active INTEGER DEFAULT 1,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS distill_runs(
  id TEXT PRIMARY KEY, space_id TEXT, status TEXT DEFAULT 'running',
  detail TEXT DEFAULT '', candidates_added INTEGER DEFAULT 0,
  started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS filter_rules(
  id TEXT PRIMARY KEY, space_id TEXT DEFAULT '', pattern TEXT,
  category TEXT DEFAULT '', note TEXT DEFAULT '', active INTEGER DEFAULT 1,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS cc_projects(
  id TEXT PRIMARY KEY, space_id TEXT, name TEXT, path TEXT UNIQUE,
  enabled INTEGER DEFAULT 1, last_scan TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS cc_sessions(
  id TEXT PRIMARY KEY, project_id TEXT, space_id TEXT, file TEXT,
  session_id TEXT, title TEXT, summary TEXT,
  message_count INTEGER DEFAULT 0, imported_at TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, space_id TEXT, ref TEXT,
  type TEXT, payload JSON DEFAULT '{}', created_at TEXT);
CREATE TABLE IF NOT EXISTS adhoc_files(
  id TEXT PRIMARY KEY, space_id TEXT NOT NULL, name TEXT, kind TEXT,
  path TEXT, doc_id TEXT, chars INTEGER DEFAULT 0, md TEXT DEFAULT '',
  session_id TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, space_id TEXT, day TEXT,
  calls INTEGER DEFAULT 0, chars_in INTEGER DEFAULT 0, chars_out INTEGER DEFAULT 0,
  UNIQUE(space_id, day));
CREATE INDEX IF NOT EXISTS idx_docs_lib ON documents(library_id);
CREATE INDEX IF NOT EXISTS idx_docs_space ON documents(space_id);
CREATE INDEX IF NOT EXISTS idx_msg_sess ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_ev_space ON events(space_id, id);
CREATE INDEX IF NOT EXISTS idx_cand_space ON candidates(space_id, status);
"""


def init():
    c = conn()
    c.executescript(SCHEMA)
    # Migration for old databases: add newly introduced columns
    cols = {r[1] for r in c.execute("PRAGMA table_info(documents)")}
    for name, ddl in (("image_count", "INTEGER DEFAULT 0"),
                      ("vision", "INTEGER DEFAULT 1"),
                      ("ocr_status", "TEXT DEFAULT ''")):
        if name not in cols:
            c.execute(f"ALTER TABLE documents ADD COLUMN {name} {ddl}")
    rcols = {r[1] for r in c.execute("PRAGMA table_info(rules)")}
    for name, ddl in (("hits", "INTEGER DEFAULT 0"),
                      ("corrections", "INTEGER DEFAULT 0"),
                      ("dedup_key", "TEXT DEFAULT ''")):
        if name not in rcols:
            c.execute(f"ALTER TABLE rules ADD COLUMN {name} {ddl}")
    c.commit()


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix=""):
    return prefix + uuid.uuid4().hex[:12]


def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def x(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur.lastrowid


def log_event(space_id, ref, type_, payload=None):
    import json
    x("INSERT INTO events(space_id,ref,type,payload,created_at) VALUES(?,?,?,?,?)",
      (space_id, ref, type_, json.dumps(payload or {}, ensure_ascii=False), now()))


def bump_usage(space_id, chars_in=0, chars_out=0):
    day = time.strftime("%Y-%m-%d")
    x("INSERT INTO usage(space_id,day,calls,chars_in,chars_out) VALUES(?,?,1,?,?) "
      "ON CONFLICT(space_id,day) DO UPDATE SET calls=calls+1,"
      "chars_in=chars_in+excluded.chars_in,"
      "chars_out=chars_out+excluded.chars_out",
      (space_id, day, chars_in, chars_out))
