"""SQLite 账本：schema 迁移与连接。WAL 模式允许 server 读 + daemon 写并发。"""
import sqlite3
import threading
from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules(
  id TEXT PRIMARY KEY,                -- T1.. 编号永久不复用
  mode TEXT NOT NULL CHECK(mode IN('forbid','require')),
  statement TEXT NOT NULL,
  ex_bad TEXT DEFAULT '', ex_good TEXT DEFAULT '',
  status TEXT DEFAULT 'active' CHECK(status IN('trial','active','archived')),
  source_kind TEXT DEFAULT 'correction',
  created_at TEXT, last_hit_at TEXT,
  hits INTEGER DEFAULT 0, corrections INTEGER DEFAULT 0,
  note TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS profiles(
  id TEXT PRIMARY KEY,                -- P-A.. 代码化，陈述文本不含人名评价敏感词
  subject TEXT DEFAULT '', kind TEXT DEFAULT 'fact' CHECK(kind IN('fact','pattern')),
  statement TEXT NOT NULL,
  status TEXT DEFAULT 'active' CHECK(status IN('active','expired')),
  first_seen TEXT, last_cited_at TEXT,
  cited INTEGER DEFAULT 0, rejected INTEGER DEFAULT 0,
  ttl_days INTEGER DEFAULT 90);

CREATE TABLE IF NOT EXISTS candidates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK(kind IN('rule','profile','filter')),
  payload TEXT NOT NULL,              -- JSON 草稿
  origin TEXT DEFAULT '', evidence TEXT DEFAULT '[]',   -- JSON [{session,line,ts,quote}]
  dedup TEXT DEFAULT 'new',           -- new|duplicate|violation_repeat
  dedup_ref TEXT DEFAULT '', confidence TEXT DEFAULT 'medium',
  dedup_key TEXT DEFAULT '',          -- 机械去重键；同键 pending 合并而非堆积
  times_seen INTEGER DEFAULT 1,
  status TEXT DEFAULT 'pending' CHECK(status IN('pending','approved','rejected')),
  created_at TEXT, decided_at TEXT, decision_note TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY, slug TEXT, project_path TEXT,
  is_internal INTEGER DEFAULT 0,
  first_seen TEXT, last_event_at TEXT,
  consumed_offset INTEGER DEFAULT 0,  -- 已消费（进过蒸馏/统计）的字节偏移
  file_offset INTEGER DEFAULT 0,      -- 已解析到的字节偏移（采集水位）
  consumed_lines INTEGER DEFAULT 0,   -- 与 consumed_offset 对应的行号
  archived INTEGER DEFAULT 0,         -- 已复制进 archive/（对抗 ~30 天自动清理）
  parse_errors INTEGER DEFAULT 0, total_lines INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT,
  session_id TEXT DEFAULT '', project TEXT DEFAULT '', detail TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS filter_rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pattern TEXT UNIQUE NOT NULL, category TEXT NOT NULL,
  note TEXT DEFAULT '', status TEXT DEFAULT 'active',
  created_by TEXT DEFAULT 'seed', hits_total INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS redact_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT,
  category TEXT, session_id TEXT, line INTEGER);   -- 仅元数据，绝无原文

CREATE TABLE IF NOT EXISTS digest_versions(
  ver INTEGER PRIMARY KEY, rendered_at TEXT, bytes INTEGER,
  est_tokens INTEGER, rule_ids TEXT, nonce TEXT, stale INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS distill_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT,
  status TEXT, input_chars INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0,
  candidates_count INTEGER DEFAULT 0, rolled_chars INTEGER DEFAULT 0, note TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS errors(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, component TEXT, kind TEXT,
  dedup_key TEXT UNIQUE, count INTEGER DEFAULT 1, last_at TEXT,
  sample_ref TEXT DEFAULT '', resolved INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS projects(
  path TEXT PRIMARY KEY, connected_at TEXT,
  hook_installed INTEGER DEFAULT 0, overlay_text TEXT DEFAULT '',
  pending_since TEXT DEFAULT '', last_seen TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS usage_daily(
  day TEXT, project TEXT, scope TEXT,   -- scope: session|distill
  tokens INTEGER DEFAULT 0, PRIMARY KEY(day, project, scope));

CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_cand_status ON candidates(status);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        config.ensure_dirs()
        c = sqlite3.connect(str(config.DB_PATH), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=8000")
        _local.conn = c
    return c


def reset():
    """关闭并清除本线程连接（测试/搬家用）。"""
    c = getattr(_local, "conn", None)
    if c is not None:
        try:
            c.close()
        except sqlite3.Error:
            pass
    _local.conn = None


def init_db():
    c = conn()
    c.executescript(SCHEMA)
    for table, col in (("sessions", "consumed_lines INTEGER DEFAULT 0"),
                      ("sessions", "archived INTEGER DEFAULT 0"),
                      ("candidates", "dedup_key TEXT DEFAULT ''"),
                      ("candidates", "times_seen INTEGER DEFAULT 1"),
                      ("projects", "last_seen TEXT DEFAULT ''")):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        except sqlite3.OperationalError:      # 列已存在
            pass
    c.commit()


def now() -> str:
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def kv_get(key: str, default: str = "") -> str:
    r = conn().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def kv_set(key: str, value: str):
    c = conn()
    c.execute("INSERT INTO kv(key,value) VALUES(?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    c.commit()
