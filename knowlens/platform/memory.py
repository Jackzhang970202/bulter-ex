# -*- coding: utf-8 -*-
"""Layered memory with human approval.

Scopes: session (messages, handled by runtime), user, agent, space, global.
Only approved memories are injected into prompts. New candidates may come from
user corrections, failed runs or explicit authoring.
"""
import re
import time

from .. import db
from . import services

SCOPES = ("user", "agent", "space", "global")
KINDS = ("profile", "preference", "experience", "fact", "failure")


def add(scope, ref_id, kind, content, source="manual", confidence=0.7,
        status="pending", user_id=""):
    content = (content or "").strip()
    if not content:
        return None
    if scope not in SCOPES:
        scope = "agent"
    if kind not in KINDS:
        kind = "fact"
    now = services.now()
    # exact duplicate in the same scope becomes a hit, not a new row
    dup = db.one("SELECT * FROM pf_memories WHERE scope=? AND ref_id=? AND "
                 "content=? AND status=?",
                 (scope, ref_id or "", content, status))
    if dup:
        db.x("UPDATE pf_memories SET hits=hits+1,updated_at=? WHERE id=?",
             (now, dup["id"]))
        return db.one("SELECT * FROM pf_memories WHERE id=?", (dup["id"],))
    mid = services.uid("mm_")
    db.x("INSERT INTO pf_memories(id,scope,ref_id,kind,content,confidence,hits,"
         "status,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
         (mid, scope, ref_id or "", kind, content[:4000], float(confidence),
          0, status, source, now, now))
    services.audit(user_id, "memory.add", mid,
                   f"{scope}:{ref_id} {content[:80]}")
    return db.one("SELECT * FROM pf_memories WHERE id=?", (mid,))


def list_memories(scope="", ref_id="", status="", q="", limit=200):
    sql = "SELECT * FROM pf_memories WHERE 1=1"
    args = []
    if scope:
        sql += " AND scope=?"
        args.append(scope)
    if ref_id:
        sql += " AND ref_id=?"
        args.append(ref_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    rows = db.q(sql, tuple(args))
    if q:
        rows = [r for r in rows
                if services.score_text(q, r["content"], r["kind"]) > 0]
    return rows


def set_status(memory_id, status, user_id=""):
    if status not in ("pending", "active", "rejected"):
        return None
    db.x("UPDATE pf_memories SET status=?,updated_at=? WHERE id=?",
         (status, services.now(), memory_id))
    services.audit(user_id, f"memory.{status}", memory_id, "")
    return db.one("SELECT * FROM pf_memories WHERE id=?", (memory_id,))


def delete(memory_id, user_id=""):
    db.x("DELETE FROM pf_memories WHERE id=?", (memory_id,))
    services.audit(user_id, "memory.delete", memory_id, "")
    return True


def context(agent_id="", user_id="", space_id="", query="", limit=6):
    """Approved memories relevant to this agent/user/space, best first."""
    clauses = []
    args = []
    for scope, ref in (("agent", agent_id), ("user", user_id),
                       ("space", space_id), ("global", "")):
        if scope == "global" or ref:
            clauses.append("(scope=? AND ref_id=?)")
            args.extend([scope, ref or ""])
    if not clauses:
        return []
    sql = ("SELECT * FROM pf_memories WHERE status='active' AND ("
           + " OR ".join(clauses) + ")")
    rows = db.q(sql, tuple(args))
    now = time.time()

    def score(row):
        rel = services.score_text(query, row["content"], row["kind"])
        conf = float(row.get("confidence") or 0.5)
        try:
            age_days = max(0.0, (now - time.mktime(time.strptime(
                row["updated_at"], "%Y-%m-%d %H:%M:%S"))) / 86400)
        except Exception:
            age_days = 0
        recency = 1.0 / (1.0 + age_days / 30.0)
        hits = min(1.0, (row.get("hits") or 0) / 20.0)
        return rel * 2 + conf + recency * 0.5 + hits * 0.3

    ranked = sorted(rows, key=score, reverse=True)[:limit]
    for row in ranked:
        try:
            db.x("UPDATE pf_memories SET hits=hits+1 WHERE id=?", (row["id"],))
        except Exception:
            pass
    return ranked


def extract_feedback(agent_id, user_id, space_id, question, correction,
                     verdict=""):
    """Negative feedback becomes a pending experience/failure candidate."""
    if not correction:
        return None
    kind = "failure" if verdict in ("not_found", "不能") else "experience"
    content = f"问题：{question[:300]}\n纠正/经验：{correction[:1200]}"
    return add("agent", agent_id, kind, content, source="feedback",
               confidence=0.7, status="pending", user_id=user_id)


def extract_failed_run(agent_id, question, error, user_id=""):
    content = f"失败任务：{(question or '')[:300]}\n错误：{(error or '')[:600]}"
    return add("agent", agent_id, "failure", content, source="failed_run",
               confidence=0.6, status="pending", user_id=user_id)


def consolidate(scope, ref_id, user_id=""):
    """Drop near-duplicates, keeping the most confident/most recent row."""
    rows = list_memories(scope, ref_id, limit=500)
    seen = {}
    removed = 0
    for row in rows:
        key = re.sub(r"[^a-z0-9一-鿿]+", "", (row["content"] or "").lower())[:120]
        if not key:
            continue
        keep = seen.get(key)
        if not keep:
            seen[key] = row
            continue
        loser = row if (row["confidence"] or 0) <= (keep["confidence"] or 0) else keep
        winner = keep if loser is row else row
        seen[key] = winner
        db.x("DELETE FROM pf_memories WHERE id=?", (loser["id"],))
        removed += 1
    aged = db.conn().execute(
        "DELETE FROM pf_memories WHERE status='active' AND hits=0 "
        "AND updated_at < date('now','-180 day')").rowcount
    db.conn().commit()
    services.audit(user_id, "memory.consolidate", f"{scope}:{ref_id}",
                   f"removed={removed} aged={aged}")
    return {"removed": removed, "aged": aged, "kept": len(seen)}