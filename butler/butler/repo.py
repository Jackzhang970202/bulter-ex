"""域操作层：候选→审批→晋升、规则/画像 CRUD、项目、事件、错误、大屏统计。"""
import json
import string
from datetime import datetime, timedelta
from . import config, db, filterpipe, parser as _parser


# ---------- 基础设施 ----------
def event(kind: str, session: str = "", project: str = "", detail: dict | None = None):
    c = db.conn()
    c.execute("INSERT INTO events(ts,kind,session_id,project,detail) VALUES(?,?,?,?,?)",
              (db.now(), kind, session, project,
               json.dumps(detail or {}, ensure_ascii=False)))
    c.commit()
    _touch_render_if_needed(kind)


def _touch_render_if_needed(kind):
    if kind in ("promote", "rule_edit", "render", "archive_passive"):
        db.kv_set("digest_dirty", db.now())


def log_error(component: str, kind: str, sample: str = ""):
    c = db.conn()
    key = f"{component}:{kind}"
    c.execute("INSERT INTO errors(ts,component,kind,dedup_key,last_at,sample_ref)"
              " VALUES(?,?,?,?,?,?) ON CONFLICT(dedup_key) DO UPDATE SET"
              " count=count+1, last_at=excluded.last_at, sample_ref=excluded.sample_ref,"
              " resolved=0",                                     # 复发即重新亮红
              (db.now(), component, kind, key, db.now(), (sample or "")[:200]))
    c.commit()


def resolve_errors(kind: str = ""):
    c = db.conn()
    if kind:
        c.execute("UPDATE errors SET resolved=1 WHERE dedup_key=?", (kind,))
    else:
        c.execute("UPDATE errors SET resolved=1 WHERE resolved=0")
    c.commit()


def heartbeat(name: str):
    db.kv_set(f"hb_{name}", db.now())


def hb_age(name: str) -> int:
    """心跳距今秒数；-1=从未有心跳。"""
    v = db.kv_get(f"hb_{name}")
    if not v:
        return -1
    try:
        return int((datetime.now() - datetime.fromisoformat(v)).total_seconds())
    except ValueError:
        return -1


def set_pause(hours: float):
    until = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
    db.kv_set("paused_until", until)
    event("pause", detail={"until": until})


def is_paused() -> bool:
    v = db.kv_get("paused_until")
    if not v:
        return False
    try:
        return datetime.fromisoformat(v) > datetime.now()
    except ValueError:
        return False


def invalidate_render():
    db.kv_set("digest_dirty", db.now())


# ---------- 编号分配 ----------
def _next_rule_id() -> str:
    ids = [r["id"] for r in db.conn().execute("SELECT id FROM rules")]
    n = max([int(i[1:]) for i in ids if i[1:].isdigit()], default=0)
    return f"T{n + 1}"


def _next_profile_id() -> str:
    used = {r["id"][2:] for r in db.conn().execute("SELECT id FROM profiles")
            if len(r["id"]) > 2 and r["id"][1] == "-"}
    for ch in list(string.ascii_uppercase) + [f"{a}{b}" for a in string.ascii_uppercase
                                              for b in string.ascii_uppercase]:
        if ch not in used:
            return f"P-{ch}"
    return "P-ZZ"


# ---------- 候选（含机械去重） ----------
def _norm(s: str) -> str:
    import re
    return re.sub(r"\s+", "", (s or "").lower().strip())


_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def add_candidate(kind: str, payload: dict, origin: str = "",
                  evidence: list | None = None, dedup: str = "new",
                  dedup_ref: str = "", confidence: str = "medium"):
    """入库级去重：
    1) 与已批准/已拒绝的同键候选不再重复出现（拒绝即静默，批准即转化）；
    2) 文本与现有规则完全同义 → 自动转 violation_repeat（反复违反信号）；
       画像/过滤完全同义 → 丢弃；
    3) 已 pending 的同键候选 → 合并证据 + times_seen+1，不堆新卡片。"""
    c = db.conn()
    stmt = payload.get("statement") or payload.get("pattern") or ""
    norm = _norm(stmt)
    if not norm:
        return 0

    # 2) 与现有库同义
    if kind == "rule" and dedup == "new":
        hit = next((r["id"] for r in c.execute("SELECT id,statement FROM rules "
                  "WHERE status<>'archived'") if _norm(r["statement"]) == norm), None)
        if hit:
            dedup, dedup_ref = "violation_repeat", hit
            c.execute("UPDATE rules SET corrections=corrections+1 WHERE id=?", (hit,))
            c.commit()
        elif any(_norm(r["statement"]) == norm for r in
                 c.execute("SELECT statement FROM rules")):
            return 0                                  # 已归档过同义条目，不再骚扰
    elif kind == "profile":
        if any(_norm(r["statement"]) == norm for r in
               c.execute("SELECT statement FROM profiles WHERE status='active'")):
            return 0
    elif kind == "filter":
        if c.execute("SELECT 1 FROM filter_rules WHERE status='active' AND pattern=?",
                     (stmt,)).fetchone():
            return 0

    key = f"{kind}|{dedup}|{dedup_ref}|{norm}"

    # 1) 历史处置：被拒绝过/批准过的同键候选不再重复出现（rule 的反复违反会在
    #    上面转成 violation_repeat 新键，走"改写建议"通道，不受此限）
    hist = [r["status"] for r in c.execute(
        "SELECT status FROM candidates WHERE dedup_key=?", (key,))]
    if "rejected" in hist or "approved" in hist:
        return 0

    # 3) 合并进已有 pending
    pend = c.execute("SELECT * FROM candidates WHERE status='pending' AND dedup_key=?",
                     (key,)).fetchone()
    if pend:
        old_ev = json.loads(pend["evidence"] or "[]")
        new_ev = old_ev + [e for e in (evidence or [])
                           if not any(e.get("session") == o.get("session")
                                      and e.get("line") == o.get("line") for o in old_ev)]
        merged = new_ev[-10:]
        new_orig = (pend["origin"] + " · " + origin)[-160:]
        new_conf = confidence if _CONF_RANK.get(confidence, 1) > _CONF_RANK.get(
            pend["confidence"], 1) else pend["confidence"]
        c.execute("UPDATE candidates SET evidence=?, origin=?, times_seen=times_seen+1,"
                  " confidence=?, payload=? WHERE id=?",
                  (json.dumps(merged, ensure_ascii=False), new_orig, new_conf,
                   json.dumps(payload, ensure_ascii=False), pend["id"]))
        c.commit()
        event("candidate_merge", detail={"id": pend["id"], "times_seen": (pend["times_seen"] or 1) + 1})
        return pend["id"]

    cur = c.execute(
        "INSERT INTO candidates(kind,payload,origin,evidence,dedup,dedup_ref,"
        "confidence,dedup_key,times_seen,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,1,'pending',?)",
        (kind, json.dumps(payload, ensure_ascii=False), origin,
         json.dumps(evidence or [], ensure_ascii=False), dedup, dedup_ref,
         confidence, key, db.now()))
    c.commit()
    event("candidate", detail={"id": cur.lastrowid, "kind": kind, "dedup": dedup})
    return cur.lastrowid


def candidates_list(only_pending=True):
    q = "SELECT * FROM candidates " + ("WHERE status='pending' " if only_pending else "") \
        + "ORDER BY id DESC LIMIT 200"
    rows = []
    for r in db.conn().execute(q):
        d = dict(r)
        d["payload"] = json.loads(d["payload"] or "{}")
        d["evidence"] = json.loads(d["evidence"] or "[]")
        rows.append(d)
    return rows


def decide_candidate(cid: int, action: str, edited: dict | None = None, note: str = ""):
    """approve 晋升入库 / reject。晋升校验：rule 必填正反例（新语义：require/forbid）。"""
    c = db.conn()
    row = c.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if not row or row["status"] != "pending":
        return False
    payload = json.loads(row["payload"])
    if edited:
        payload.update(edited)
    kind, dedup, ref = row["kind"], row["dedup"], row["dedup_ref"]

    if action == "approve":
        if kind == "rule":
            if dedup == "violation_repeat" and ref:
                st = payload.get("statement") or "（沿用）"
                c.execute("UPDATE rules SET statement=?, ex_bad=?, ex_good=?, mode=?,"
                          " note=? WHERE id=?",
                          (st, payload.get("ex_bad", ""), payload.get("ex_good", ""),
                           payload.get("mode", "forbid"),
                           f"经复盘改写@{db.now()}", ref))
            else:
                if not payload.get("statement"):
                    return False
                rid = _next_rule_id()
                c.execute("INSERT INTO rules(id,mode,statement,ex_bad,ex_good,status,"
                          "source_kind,created_at) VALUES(?,?,?,?,?,'active','promotion',?)",
                          (rid, payload.get("mode", "forbid"), payload["statement"],
                           payload.get("ex_bad", ""), payload.get("ex_good", ""), db.now()))
        elif kind == "profile":
            if not payload.get("statement"):
                return False
            pid = _next_profile_id()
            c.execute("INSERT INTO profiles(id,subject,kind,statement,status,first_seen,"
                      "ttl_days) VALUES(?,?,?,?, 'active', ?,?)",
                      (pid, payload.get("subject", ""),
                       payload.get("kind", "fact"), payload["statement"],
                       db.now(), int(payload.get("ttl_days", 90))))
        elif kind == "filter":
            filterpipe.add_rule_from_candidate(
                payload.get("pattern", ""), payload.get("category", "secret"),
                payload.get("note", "复盘提议"))
        c.execute("UPDATE candidates SET status='approved', decided_at=?, decision_note=?"
                  " WHERE id=?", (db.now(), note, cid))
        c.commit()
        event("promote", detail={"candidate": cid, "kind": kind})
        invalidate_render()
        return True
    if action == "reject":
        c.execute("UPDATE candidates SET status='rejected', decided_at=?, decision_note=?"
                  " WHERE id=?", (db.now(), note, cid))
        c.commit()
        event("reject", detail={"candidate": cid})
        return True
    return False


def bump_rule_stats(rule_id: str, corrections: int = 0):
    c = db.conn()
    if rule_id and corrections:
        c.execute("UPDATE rules SET corrections=corrections+?, last_hit_at=? WHERE id=?",
                  (corrections, db.now(), rule_id))
    c.commit()


# ---------- 规则/画像直接编辑 ----------
def update_rule(rid: str, **fields):
    c = db.conn()
    allowed = {"statement", "mode", "ex_bad", "ex_good", "status", "note"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if sets:
        c.execute("UPDATE rules SET " + ",".join(f"{k}=?" for k in sets) + " WHERE id=?",
                  (*sets.values(), rid))
        c.commit()
        event("rule_edit", detail={"id": rid, **sets})
        invalidate_render()


def passive_expire():
    """画像 TTL：超期未引用→expired；返回过期数。"""
    c = db.conn()
    n = 0
    for r in c.execute("SELECT * FROM profiles WHERE status='active'").fetchall():
        anchor = r["last_cited_at"] or r["first_seen"]
        try:
            age = (datetime.now() - datetime.fromisoformat(anchor)).days
        except (TypeError, ValueError):
            continue
        if age > r["ttl_days"]:
            c.execute("UPDATE profiles SET status='expired' WHERE id=?", (r["id"],))
            n += 1
    if n:
        c.commit()
        event("archive_passive", detail={"profiles_expired": n})
        invalidate_render()
    return n


def update_profile(pid: str, **fields):
    c = db.conn()
    allowed = {"statement", "status", "kind", "ttl_days"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if sets:
        c.execute("UPDATE profiles SET " + ",".join(f"{k}=?" for k in sets) + " WHERE id=?",
                  (*sets.values(), pid))
        c.commit()
        event("profile_edit", detail={"id": pid, **sets})
        invalidate_render()


# ---------- 项目 ----------
def project_seen(path: str):
    """transcript 里出现过的真实 cwd → 刷新活跃度；近 3 天有活动且未接入才进待确认。
    （历史项目不再刷屏：待确认队列只反映"你最近真的在用它"）"""
    if not path:
        return
    c = db.conn()
    now = db.now()
    row = c.execute("SELECT * FROM projects WHERE path=?", (path,)).fetchone()
    if row is None:
        c.execute("INSERT INTO projects(path,pending_since,last_seen) VALUES(?,?,?)",
                  (path, now, now))
        c.commit()
        event("project_pending", project=path)
    else:
        upd = {"last_seen": now}
        if not row["hook_installed"]:
            ps = row["pending_since"] or ""
            fresh = True
            try:
                fresh = (datetime.now() - datetime.fromisoformat(ps)).days >= 3 if ps else True
            except ValueError:
                pass
            if fresh:
                upd["pending_since"] = now
        c.execute("UPDATE projects SET " + ",".join(f"{k}=?" for k in upd) +
                  " WHERE path=?", (*upd.values(), path))
        c.commit()


def _recent_iso(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def pending_projects():
    """待确认 = 近 3 天有会话活动 且 未接入 且 未被跳过（按活跃度排序，最多 20）。"""
    return [dict(r) for r in db.conn().execute(
        "SELECT * FROM projects WHERE hook_installed=0 AND pending_since<>'' "
        "AND last_seen>=? ORDER BY last_seen DESC LIMIT 20", (_recent_iso(3),))]


def connected_projects():
    return [dict(r) for r in db.conn().execute(
        "SELECT * FROM projects WHERE hook_installed=1 ORDER BY connected_at")]


def mark_connected(path: str):
    c = db.conn()
    c.execute("INSERT INTO projects(path) VALUES(?) ON CONFLICT(path) DO NOTHING", (path,))
    c.execute("UPDATE projects SET hook_installed=1, connected_at=?, pending_since=''"
              " WHERE path=?", (db.now(), path))
    c.commit()
    event("connect", project=path)
    invalidate_render()


def mark_uninstalled(path: str):
    c = db.conn()
    c.execute("UPDATE projects SET hook_installed=0, pending_since=? WHERE path=?",
              (db.now(), path))
    c.commit()
    event("uninstall", project=path)


def skip_project(path: str):
    db.conn().execute("UPDATE projects SET pending_since='' WHERE path=?", (path,))
    db.conn().commit()
    event("project_skip", project=path)


def set_overlay(path: str, text: str):
    db.conn().execute("UPDATE projects SET overlay_text=? WHERE path=?", (text, path))
    db.conn().commit()
    invalidate_render()


# ---------- 注入观测（hook.cmd 写的日志） ----------
def inject_stats_24h():
    ok = miss = 0
    try:
        since = datetime.now() - timedelta(hours=24)
        for ln in config.INJECT_LOG.read_text("utf-8", errors="replace").splitlines()[-2000:]:
            parts = ln.split()
            if len(parts) < 4:
                continue
            ts = None
            for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
                try:
                    ts = datetime.strptime(
                        f"{parts[2]} {parts[3].split('.')[0].strip()}", fmt)
                    break
                except ValueError:
                    continue
            if ts is None:
                continue
            if ts < since:
                continue
            if parts[0] == "ok":
                ok += 1
            elif parts[0] == "miss":
                miss += 1
    except OSError:
        pass
    return ok, miss


# ---------- 证据回放（优先读原文完整行；出口必须再过一遍过滤） ----------
def evidence_quotes(evidence: list):
    out = []
    for ev in evidence or []:
        sid, line = ev.get("session", ""), int(ev.get("line", 0) or 0)
        text, from_file = ev.get("quote", ""), False
        if sid and line:
            for base in (config.ARCHIVE, config.CLAUDE_PROJECTS):
                if from_file:
                    break
                for p in base.rglob(f"{sid}.jsonl"):
                    try:
                        with open(p, "r", encoding="utf-8", errors="replace") as f:
                            for i, raw in enumerate(f, 1):
                                if i == line:
                                    d = _parser.parse_line(raw)
                                    t = (d or {}).get("text", "")
                                    if t:
                                        text, from_file = t, True
                                    break
                    except OSError:
                        continue
                    if from_file:
                        break
        masked, _ = filterpipe.mask_text(text[:1600])
        out.append({"session": sid, "line": line, "ts": ev.get("ts", ""),
                    "quote": masked, "full": from_file and len(text) <= 1600})
    return out


# ---------- 大屏聚合 ----------
def stats():
    c = db.conn()
    today = datetime.now().strftime("%Y-%m-%d")
    pend = c.execute("SELECT COUNT(*) n FROM candidates WHERE status='pending'").fetchone()["n"]
    usage_sess = c.execute("SELECT COALESCE(SUM(tokens),0) t FROM usage_daily "
                           "WHERE day=? AND scope='session'", (today,)).fetchone()["t"]
    usage_dist = c.execute("SELECT COALESCE(SUM(tokens),0) t FROM usage_daily "
                           "WHERE day=? AND scope='distill'", (today,)).fetchone()["t"]
    run = c.execute("SELECT * FROM distill_runs ORDER BY id DESC LIMIT 1").fetchone()
    last_ok = c.execute("SELECT finished FROM distill_runs WHERE status='ok' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    hours_since = -1
    if last_ok:
        try:
            hours_since = int((datetime.now()
                               - datetime.fromisoformat(last_ok["finished"])).total_seconds() // 3600)
        except ValueError:
            pass
    unread = c.execute("SELECT COUNT(*) n FROM sessions WHERE is_internal=0 "
                       "AND file_offset>consumed_offset").fetchone()["n"]
    ok24, miss24 = inject_stats_24h()
    digest_row = c.execute("SELECT * FROM digest_versions ORDER BY ver DESC LIMIT 1").fetchone()
    s = dict(
        pending=pend,
        usage_today={"session": usage_sess, "distill": usage_dist,
                     "total": usage_sess + usage_dist},
        last_distill=dict(run) if run else None,
        hours_since_distill=hours_since,
        undigested_sessions=unread,
        inject_24h={"ok": ok24, "miss": miss24},
        digest=dict(digest_row) if digest_row else None,
        digest_dirty=bool(db.kv_get("digest_dirty")),
        hb={"collector": hb_age("collector"), "server": hb_age("server")},
        paused=is_paused(),
        distilling=db.kv_get("distilling") == "1",
        distill_started_at=db.kv_get("distill_started_at"),
        errors=[dict(r) for r in c.execute(
            "SELECT * FROM errors WHERE resolved=0 ORDER BY last_at DESC LIMIT 8")],
        events=[dict(r) for r in c.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT 30")],
        candidates=candidates_list(True),
        rules=[dict(r) for r in c.execute(
            "SELECT * FROM rules WHERE status<>'archived' ORDER BY id")],
        archived_rules=[dict(r) for r in c.execute(
            "SELECT * FROM rules WHERE status='archived' ORDER BY id DESC LIMIT 50")],
        expired_profiles=[dict(r) for r in c.execute(
            "SELECT * FROM profiles WHERE status='expired' ORDER BY id DESC LIMIT 50")],
        active_rule_count=c.execute(
            "SELECT COUNT(*) n FROM rules WHERE status='active'").fetchone()["n"],
        profiles=[dict(r) for r in c.execute(
            "SELECT * FROM profiles WHERE status='active' ORDER BY id")],
        filters={"rules": c.execute(
            "SELECT COUNT(*) n FROM filter_rules WHERE status='active'").fetchone()["n"],
            "by_category": {r["category"]: r["t"] for r in c.execute(
                "SELECT category, SUM(hits_total) t FROM filter_rules GROUP BY category")}},
        distills=[dict(r) for r in c.execute(
            "SELECT * FROM distill_runs ORDER BY id DESC LIMIT 10")],
        pending_connects=pending_projects(),
        connected=connected_projects(),
        usage_7d=[dict(r) for r in c.execute(
            "SELECT day, scope, SUM(tokens) tokens FROM usage_daily "
            "WHERE day >= ? GROUP BY day, scope ORDER BY day",
            ((datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d"),))],
        settings={k: v for k, v in config.load_settings().items()},
    )
    return s
