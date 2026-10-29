# -*- coding: utf-8 -*-
"""Additive schema for the platform layer (pf_* tables).

Existing KnowLens tables are never altered here. All statements are idempotent
so an old database upgrades in place on the next start.
"""
import json

from .. import db

DDL = """
CREATE TABLE IF NOT EXISTS pf_users(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT DEFAULT 'viewer',
  email TEXT DEFAULT '', active INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_agents(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, title TEXT DEFAULT '',
  description TEXT DEFAULT '', avatar TEXT DEFAULT '', category TEXT DEFAULT 'general',
  system_prompt TEXT DEFAULT '', model TEXT DEFAULT '', temperature REAL DEFAULT 0.2,
  max_steps INTEGER DEFAULT 6, space_id TEXT DEFAULT '', library_ids JSON DEFAULT '[]',
  kb_ids JSON DEFAULT '[]',
  skill_ids JSON DEFAULT '[]', tool_ids JSON DEFAULT '[]', memory_enabled INTEGER DEFAULT 1,
  status TEXT DEFAULT 'draft', version INTEGER DEFAULT 0, owner_id TEXT DEFAULT '',
  tags JSON DEFAULT '[]', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS pf_agent_versions(
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, version INTEGER NOT NULL,
  snapshot JSON DEFAULT '{}', created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_skills(
  id TEXT PRIMARY KEY, catalog_id TEXT DEFAULT '', name TEXT NOT NULL,
  title TEXT DEFAULT '', description TEXT DEFAULT '', category TEXT DEFAULT 'general',
  kind TEXT DEFAULT 'prompt', source_url TEXT DEFAULT '', license TEXT DEFAULT '',
  content TEXT DEFAULT '', config JSON DEFAULT '{}', files JSON DEFAULT '[]',
  package_dir TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
  version TEXT DEFAULT '', installs INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS pf_experts(
  id TEXT PRIMARY KEY, agent_id TEXT DEFAULT '', name TEXT NOT NULL,
  title TEXT DEFAULT '', bio TEXT DEFAULT '', expertise JSON DEFAULT '[]',
  tags JSON DEFAULT '[]', rating REAL DEFAULT 0, uses INTEGER DEFAULT 0,
  status TEXT DEFAULT 'draft', featured INTEGER DEFAULT 0, owner_id TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS pf_teams(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
  strategy TEXT DEFAULT 'roundtable', members JSON DEFAULT '[]',
  synthesizer_id TEXT DEFAULT '', max_turns INTEGER DEFAULT 4,
  kb_ids JSON DEFAULT '[]',
  status TEXT DEFAULT 'draft', owner_id TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS pf_workflows(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
  nodes JSON DEFAULT '[]', input_schema JSON DEFAULT '[]',
  kb_ids JSON DEFAULT '[]',
  status TEXT DEFAULT 'draft', version INTEGER DEFAULT 0,
  owner_id TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS pf_runs(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref_id TEXT DEFAULT '', title TEXT DEFAULT '',
  status TEXT DEFAULT 'running', input JSON DEFAULT '{}', output TEXT DEFAULT '',
  error TEXT DEFAULT '', prompt TEXT DEFAULT '', user_id TEXT DEFAULT '',
  space_id TEXT DEFAULT '', tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
  ms INTEGER DEFAULT 0, cancel_requested INTEGER DEFAULT 0, executor TEXT DEFAULT '',
  created_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS pf_run_steps(
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, seq INTEGER DEFAULT 0,
  type TEXT DEFAULT '', name TEXT DEFAULT '', input JSON DEFAULT '{}',
  output TEXT DEFAULT '', status TEXT DEFAULT 'ok', ms INTEGER DEFAULT 0,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_sessions(
  id TEXT PRIMARY KEY, kind TEXT DEFAULT 'agent', ref_id TEXT DEFAULT '',
  user_id TEXT DEFAULT '', title TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS pf_messages(
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT DEFAULT 'user',
  content TEXT DEFAULT '', meta JSON DEFAULT '{}', created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_memories(
  id TEXT PRIMARY KEY, scope TEXT DEFAULT 'agent', ref_id TEXT DEFAULT '',
  kind TEXT DEFAULT 'fact', content TEXT NOT NULL, confidence REAL DEFAULT 0.6,
  hits INTEGER DEFAULT 0, status TEXT DEFAULT 'pending', source TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS pf_evals(
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, name TEXT DEFAULT '',
  cases JSON DEFAULT '[]', created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_eval_runs(
  id TEXT PRIMARY KEY, eval_id TEXT NOT NULL, agent_id TEXT DEFAULT '',
  version INTEGER DEFAULT 0, score REAL DEFAULT 0, detail JSON DEFAULT '[]',
  created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_schedules(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT DEFAULT 'agent',
  ref_id TEXT DEFAULT '', cron TEXT DEFAULT '0 9 * * *', enabled INTEGER DEFAULT 1,
  catch_up INTEGER DEFAULT 0, last_run TEXT DEFAULT '', next_run TEXT DEFAULT '',
  created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_schedule_runs(
  id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, run_id TEXT DEFAULT '',
  status TEXT DEFAULT 'running', started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS pf_audit(
  id TEXT PRIMARY KEY, user_id TEXT DEFAULT '', action TEXT DEFAULT '',
  target TEXT DEFAULT '', detail TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_artifacts(
  id TEXT PRIMARY KEY, run_id TEXT DEFAULT '', name TEXT DEFAULT '',
  kind TEXT DEFAULT 'text', path TEXT DEFAULT '', bytes INTEGER DEFAULT 0,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_skill_packs(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
  skill_ids JSON DEFAULT '[]', owner_id TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS pf_notifications(
  id TEXT PRIMARY KEY, user_id TEXT DEFAULT '', kind TEXT DEFAULT 'info',
  title TEXT DEFAULT '', body TEXT DEFAULT '', link TEXT DEFAULT '',
  read INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_attachments(
  id TEXT PRIMARY KEY, user_id TEXT DEFAULT '', name TEXT DEFAULT '',
  kind TEXT DEFAULT '', path TEXT DEFAULT '', chars INTEGER DEFAULT 0,
  text TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS pf_shares(
  id TEXT PRIMARY KEY, token TEXT UNIQUE, kind TEXT DEFAULT 'snapshot',
  ref_id TEXT DEFAULT '', title TEXT DEFAULT '', payload JSON DEFAULT '{}',
  password_hash TEXT DEFAULT '', views INTEGER DEFAULT 0,
  created_by TEXT DEFAULT '', created_at TEXT, expires_at TEXT);
CREATE TABLE IF NOT EXISTS pf_settings(
  key TEXT PRIMARY KEY, value TEXT DEFAULT '', updated_at TEXT);
CREATE INDEX IF NOT EXISTS idx_pf_agents_status ON pf_agents(status);
CREATE INDEX IF NOT EXISTS idx_pf_skills_catalog ON pf_skills(catalog_id);
CREATE INDEX IF NOT EXISTS idx_pf_runs_ref ON pf_runs(kind, ref_id, created_at);
CREATE INDEX IF NOT EXISTS idx_pf_steps_run ON pf_run_steps(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_pf_msg_session ON pf_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_pf_mem_scope ON pf_memories(scope, ref_id, status);
CREATE INDEX IF NOT EXISTS idx_pf_audit_time ON pf_audit(created_at);
CREATE INDEX IF NOT EXISTS idx_pf_notif_user ON pf_notifications(user_id, read, created_at);
"""


def init():
    c = db.conn()
    c.executescript(DDL)
    fcols = {r[1] for r in c.execute("PRAGMA table_info(pf_skills)")}
    for name, ddl in (("files", "JSON DEFAULT '[]'"),
                      ("package_dir", "TEXT DEFAULT ''")):
        if name not in fcols:
            c.execute(f"ALTER TABLE pf_skills ADD COLUMN {name} {ddl}")
    scols = {r[1] for r in c.execute("PRAGMA table_info(pf_shares)")}
    for name, ddl in (("password_hash", "TEXT DEFAULT ''"),
                      ("views", "INTEGER DEFAULT 0")):
        if name not in scols:
            c.execute(f"ALTER TABLE pf_shares ADD COLUMN {name} {ddl}")
    cols = {r[1] for r in c.execute("PRAGMA table_info(pf_workflows)")}
    if "input_schema" not in cols:
        c.execute("ALTER TABLE pf_workflows ADD COLUMN input_schema JSON "
                  "DEFAULT '[]'")
    if "kb_ids" not in cols:
        c.execute("ALTER TABLE pf_workflows ADD COLUMN kb_ids JSON "
                  "DEFAULT '[]'")
    for table in ("pf_agents", "pf_teams"):
        tcols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if "kb_ids" not in tcols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN kb_ids JSON "
                      "DEFAULT '[]'")
    c.commit()
    seed()


def seed():
    """Insert the default identity set once; never overwrite existing rows."""
    if db.one("SELECT COUNT(*) c FROM pf_users")["c"]:
        return
    now = db.now()
    rows = [
        ("u_admin", "张启元", "admin", "admin@knowlens.local"),
        ("u_editor", "内容编辑", "editor", "editor@knowlens.local"),
        ("u_viewer", "只读成员", "viewer", "viewer@knowlens.local"),
    ]
    for uid, name, role, email in rows:
        db.x("INSERT INTO pf_users(id,name,role,email,active,created_at) "
             "VALUES(?,?,?,?,1,?)", (uid, name, role, email, now))
    defaults = {
        "platform.name": "知鉴 KnowLens",
        "platform.theme": "light",
        "platform.default_agent": "",
        "platform.model": "",
        "platform.locale": "zh-CN",
    }
    for k, v in defaults.items():
        db.x("INSERT OR IGNORE INTO pf_settings(key,value,updated_at) "
             "VALUES(?,?,?)", (k, json.dumps(v, ensure_ascii=False), now))