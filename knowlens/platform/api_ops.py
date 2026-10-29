# -*- coding: utf-8 -*-
"""Platform operations: overview, search, users, audit, reviews, artifacts."""
import csv
import datetime as dt
import hashlib
import io
import json
import os
import queue
import secrets
import threading

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from .. import config, db
from . import (auth, excel as pf_excel, external, files as pf_files,
               llm_client, services, webingest)

router = APIRouter(prefix="/api/platform", tags=["platform-ops"])


@router.get("/health")
def health(request: Request):
    auth.current_user(request)
    return {"ok": True, "version": config.VERSION,
            "llm": llm_client.health(),
            "tables": db.one("SELECT COUNT(*) c FROM pf_agents")["c"]}


@router.get("/overview")
def overview(request: Request):
    auth.current_user(request)
    now = services.now()[:10]
    counts = {
        "agents": db.one("SELECT COUNT(*) c FROM pf_agents")["c"],
        "agents_published": db.one(
            "SELECT COUNT(*) c FROM pf_agents WHERE status='published'")["c"],
        "skills": db.one("SELECT COUNT(*) c FROM pf_skills")["c"],
        "experts": db.one("SELECT COUNT(*) c FROM pf_experts")["c"],
        "teams": db.one("SELECT COUNT(*) c FROM pf_teams")["c"],
        "workflows": db.one("SELECT COUNT(*) c FROM pf_workflows")["c"],
        "runs": db.one("SELECT COUNT(*) c FROM pf_runs")["c"],
        "runs_today": db.one(
            "SELECT COUNT(*) c FROM pf_runs WHERE created_at LIKE ?",
            (now + "%",))["c"],
        "memories": db.one(
            "SELECT COUNT(*) c FROM pf_memories WHERE status='active'")["c"],
        "pending_memories": db.one(
            "SELECT COUNT(*) c FROM pf_memories WHERE status='pending'")["c"],
        "reviews": db.one(
            "SELECT COUNT(*) c FROM pf_agents WHERE status='review'")["c"],
        "schedules": db.one(
            "SELECT COUNT(*) c FROM pf_schedules WHERE enabled=1")["c"],
    }
    usage = db.one(
        "SELECT COALESCE(SUM(tokens_in),0) tin, COALESCE(SUM(tokens_out),0) tout,"
        " COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),0) ok,"
        " COUNT(*) total FROM pf_runs")
    agents = db.q(
        "SELECT a.id,a.name,COUNT(r.id) runs FROM pf_agents a "
        "LEFT JOIN pf_runs r ON r.kind='agent' AND r.ref_id=a.id "
        "GROUP BY a.id ORDER BY runs DESC LIMIT 6")
    skills_top = db.q("SELECT id,name,installs,category FROM pf_skills "
                      "ORDER BY installs DESC LIMIT 6")
    days = {}
    for i in range(13, -1, -1):
        import datetime as dt
        day = (dt.date.today() - dt.timedelta(days=i)).isoformat()
        days[day] = {"day": day, "runs": 0, "failed": 0, "tokens": 0}
    for row in db.q("SELECT created_at,status,tokens_in,tokens_out FROM pf_runs "
                    "WHERE created_at>=?", (min(days) + " 00:00:00",)):
        day = (row["created_at"] or "")[:10]
        if day in days:
            days[day]["runs"] += 1
            days[day]["tokens"] += (row["tokens_in"] or 0) + (row["tokens_out"] or 0)
            if row["status"] == "failed":
                days[day]["failed"] += 1
    recent = db.q("SELECT id,kind,ref_id,title,status,tokens_in,tokens_out,ms,"
                  "created_at FROM pf_runs ORDER BY created_at DESC LIMIT 12")
    return {
        "counts": counts,
        "usage": {"tokens_in": usage["tin"], "tokens_out": usage["tout"],
                  "success_rate": round((usage["ok"] or 0) /
                                        (usage["total"] or 1), 3)},
        "trend": list(days.values()),
        "top_agents": agents,
        "top_skills": skills_top,
        "recent_runs": recent,
        "llm": llm_client.health(),
    }


@router.get("/catalog")
def catalog(request: Request, kind: str = "", q: str = "",
            limit: int = 60, offset: int = 0):
    auth.current_user(request)
    items = []
    wanted = {kind} if kind else {"agent", "skill", "expert", "team",
                                  "workflow", "tool"}
    if "agent" in wanted:
        for r in db.q("SELECT * FROM pf_agents"):
            items.append({"kind": "agent", "id": r["id"], "name": r["name"],
                          "title": r["title"], "description": r["description"],
                          "category": r["category"], "status": r["status"],
                          "tags": services.as_list(r["tags"]),
                          "updated_at": r["updated_at"]})
    if "skill" in wanted:
        for r in db.q("SELECT * FROM pf_skills"):
            items.append({"kind": "skill", "id": r["id"], "name": r["name"],
                          "title": r["title"], "description": r["description"],
                          "category": r["category"],
                          "status": "installed" if r["enabled"] else "disabled",
                          "tags": [r["kind"]], "updated_at": r["updated_at"]})
    if "expert" in wanted:
        for r in db.q("SELECT * FROM pf_experts"):
            items.append({"kind": "expert", "id": r["id"], "name": r["name"],
                          "title": r["title"], "description": r["bio"],
                          "category": "expert", "status": r["status"],
                          "tags": services.as_list(r["tags"]),
                          "updated_at": r["updated_at"]})
    if "team" in wanted:
        for r in db.q("SELECT * FROM pf_teams"):
            items.append({"kind": "team", "id": r["id"], "name": r["name"],
                          "title": r["name"], "description": r["description"],
                          "category": "team", "status": r["status"],
                          "tags": [r["strategy"]], "updated_at": r["updated_at"]})
    if "workflow" in wanted:
        for r in db.q("SELECT * FROM pf_workflows"):
            items.append({"kind": "workflow", "id": r["id"], "name": r["name"],
                          "title": r["name"], "description": r["description"],
                          "category": "workflow", "status": r["status"],
                          "tags": [], "updated_at": r["updated_at"]})
    if "tool" in wanted:
        from . import tools
        for r in tools.list_tools():
            items.append({"kind": "tool", "id": r["id"], "name": r["name"],
                          "title": r["name"], "description": r["description"],
                          "category": "tool", "status": "ready",
                          "tags": [r["risk"]], "updated_at": ""})
    if q:
        items = [i for i in items if services.score_text(
            q, i["name"], i["title"], i["description"], i["tags"]) > 0]
    return services.page(items, limit, offset)


@router.get("/search")
def search(request: Request, q: str = "", kind: str = "", limit: int = 30):
    auth.current_user(request)
    q = (q or "").strip()
    if not q:
        return {"items": [], "total": 0, "query": q}
    hits = []

    def add(kind_, id_, title, subtitle, jump, score):
        hits.append({"kind": kind_, "id": id_, "title": title,
                     "subtitle": subtitle, "jump": jump, "score": round(score, 3)})

    for r in db.q("SELECT * FROM pf_agents"):
        s = services.score_text(q, r["name"], r["title"], r["description"], r["tags"])
        if s:
            add("agent", r["id"], r["name"], r["description"], "#agents", s * 2)
    for r in db.q("SELECT * FROM pf_skills"):
        s = services.score_text(q, r["name"], r["title"], r["description"],
                                r["category"])
        if s:
            add("skill", r["id"], r["name"], r["description"], "#skills", s * 1.5)
    for r in db.q("SELECT * FROM pf_experts"):
        s = services.score_text(q, r["name"], r["title"], r["bio"], r["tags"])
        if s:
            add("expert", r["id"], r["name"], r["bio"], "#experts", s)
    for r in db.q("SELECT * FROM pf_teams"):
        s = services.score_text(q, r["name"], r["description"])
        if s:
            add("team", r["id"], r["name"], r["description"], "#teams", s)
    for r in db.q("SELECT * FROM pf_workflows"):
        s = services.score_text(q, r["name"], r["description"])
        if s:
            add("workflow", r["id"], r["name"], r["description"], "#workflows", s)
    for r in db.q("SELECT * FROM documents WHERE status='ready'"):
        s = services.score_text(q, r["name"], r["description"], r["summary"])
        if s:
            add("document", r["id"], r["name"], r["description"],
                f"#kb?doc={r['id']}", s)
    for r in db.q("SELECT * FROM pf_sessions ORDER BY updated_at DESC LIMIT 200"):
        s = services.score_text(q, r["title"])
        if s:
            add("session", r["id"], r["title"] or "未命名会话", r["kind"],
                "#agents", s * 0.8)
    for r in db.q("SELECT * FROM pf_memories WHERE status='active'"):
        s = services.score_text(q, r["content"])
        if s:
            add("memory", r["id"], r["content"][:60], r["scope"],
                "#evolve", s * 0.7)
    hits.sort(key=lambda x: -x["score"])
    if kind:
        hits = [h for h in hits if h["kind"] == kind]
    return {"items": hits[:max(1, min(limit, 100))], "total": len(hits),
            "query": q}


@router.get("/reviews")
def reviews(request: Request):
    auth.require(request, "admin")
    agents = db.q("SELECT id,name,title,status,owner_id,updated_at FROM pf_agents "
                  "WHERE status='review'")
    memories = db.q("SELECT id,content,scope,ref_id,source,updated_at "
                    "FROM pf_memories WHERE status='pending' LIMIT 100")
    return {"agents": agents, "memories": memories,
            "total": len(agents) + len(memories)}


@router.post("/reviews/{kind}/{item_id}")
def review_item(request: Request, kind: str, item_id: str,
                payload: dict = Body(default={})):
    user = auth.require(request, "admin")
    approve = bool(payload.get("approved", True))
    if kind == "agent":
        row = db.one("SELECT * FROM pf_agents WHERE id=?", (item_id,))
        if not row:
            raise HTTPException(404, "对象不存在")
        new = "published" if approve else "draft"
        version = (row["version"] or 0) + (1 if approve else 0)
        if approve:
            db.x("INSERT INTO pf_agent_versions(id,agent_id,version,snapshot,"
                 "created_at) VALUES(?,?,?,?,?)",
                 (services.uid("av_"), item_id, version,
                  services.jdump(row), services.now()))
        db.x("UPDATE pf_agents SET status=?,version=?,updated_at=? WHERE id=?",
             (new, version, services.now(), item_id))
        services.audit(user["id"], "review.agent", item_id, str(approve))
        return {"ok": True, "status": new}
    if kind == "memory":
        row = db.one("SELECT * FROM pf_memories WHERE id=?", (item_id,))
        if not row:
            raise HTTPException(404, "记忆不存在")
        db.x("UPDATE pf_memories SET status=?,updated_at=? WHERE id=?",
             ("active" if approve else "rejected", services.now(), item_id))
        services.audit(user["id"], "review.memory", item_id, str(approve))
        return {"ok": True}
    raise HTTPException(400, "不支持的审核类型")


# ────────────────────────── users ──────────────────────────

@router.get("/users")
def list_users(request: Request):
    auth.require(request, "admin")
    rows = db.q("SELECT * FROM pf_users ORDER BY created_at")
    return {"items": rows, "total": len(rows)}


@router.post("/users")
def create_user(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "admin")
    name = (payload.get("name") or "").strip()
    role = payload.get("role") or "viewer"
    if not name or role not in auth.ROLE_RANK:
        raise HTTPException(400, "名称或角色不合法")
    uid = payload.get("id") or services.uid("u_")
    db.x("INSERT INTO pf_users(id,name,role,email,active,created_at) "
         "VALUES(?,?,?,?,1,?)",
         (uid, name, role, payload.get("email") or "", services.now()))
    services.audit(user["id"], "user.create", uid, role)
    return db.one("SELECT * FROM pf_users WHERE id=?", (uid,))


@router.patch("/users/{user_id}")
def update_user(request: Request, user_id: str, payload: dict = Body(default={})):
    actor = auth.require(request, "admin")
    row = db.one("SELECT * FROM pf_users WHERE id=?", (user_id,))
    if not row:
        raise HTTPException(404, "用户不存在")
    sets, args = [], []
    for key in ("name", "role", "email"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(str(payload[key]))
    if "active" in payload:
        sets.append("active=?")
        args.append(1 if payload["active"] else 0)
    if sets:
        args.append(user_id)
        db.x(f"UPDATE pf_users SET {', '.join(sets)} WHERE id=?", args)
        services.audit(actor["id"], "user.update", user_id, ",".join(payload))
    return db.one("SELECT * FROM pf_users WHERE id=?", (user_id,))


@router.delete("/users/{user_id}")
def delete_user(request: Request, user_id: str):
    actor = auth.require(request, "admin")
    if user_id == actor["id"]:
        raise HTTPException(400, "不能删除当前用户")
    db.x("UPDATE pf_users SET active=0 WHERE id=?", (user_id,))
    services.audit(actor["id"], "user.disable", user_id, "")
    return {"ok": True}


@router.get("/audit")
def audit_log(request: Request, limit: int = 100, action: str = ""):
    auth.require(request, "admin")
    sql, args = "SELECT * FROM pf_audit WHERE 1=1", []
    if action:
        sql += " AND action LIKE ?"
        args.append(action + "%")
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    rows = db.q(sql, tuple(args))
    return {"items": rows, "total": len(rows)}


# ────────────────────────── artifacts & settings ──────────────────────────

@router.get("/artifacts")
def list_artifacts(request: Request, run_id: str = "", limit: int = 100):
    auth.current_user(request)
    sql, args = "SELECT * FROM pf_artifacts WHERE 1=1", []
    if run_id:
        sql += " AND run_id=?"
        args.append(run_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    rows = db.q(sql, tuple(args))
    return {"items": rows, "total": len(rows)}


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(request: Request, artifact_id: str):
    auth.current_user(request)
    row = db.one("SELECT * FROM pf_artifacts WHERE id=?", (artifact_id,))
    if not row or not row.get("path"):
        raise HTTPException(404, "产物不存在")
    root = os.path.abspath(os.path.join(config.DATA, "platform_artifacts"))
    path = os.path.abspath(row["path"])
    if not path.startswith(root + os.sep) or not os.path.exists(path):
        raise HTTPException(404, "产物文件不存在")
    return FileResponse(path, filename=os.path.basename(path))




@router.post("/files")
async def upload_files(request: Request, files: list[UploadFile] = File(...)):
    """Temporary chat attachments; never written to the knowledge base."""
    user = auth.current_user(request)
    out = []
    for upload in files[:10]:
        item, error = pf_files.save(user["id"], upload.filename,
                                    upload.file)
        if error:
            out.append({"name": upload.filename, "error": error})
        else:
            out.append(item)
    services.audit(user["id"], "files.upload", "",
                   f"{len(out)} files")
    return {"items": out}


@router.delete("/files/{attachment_id}")
def delete_file(request: Request, attachment_id: str):
    user = auth.current_user(request)
    if not pf_files.delete(attachment_id, user["id"]):
        raise HTTPException(404, "附件不存在")
    return {"ok": True}



# ────────────────────────── Excel 问数 ──────────────────────────

@router.post("/excel/upload")
async def excel_upload(request: Request, files: list[UploadFile] = File(...)):
    user = auth.current_user(request)
    out = []
    for upload in files[:5]:
        kind = (upload.filename or "").rsplit(".", 1)[-1].lower()
        if kind not in pf_excel.EXCEL_KINDS:
            out.append({"name": upload.filename, "error": "只支持 xlsx / xls / csv"})
            continue
        item, error = pf_files.save(user["id"], upload.filename, upload.file,
                                    extract=False)
        if error:
            out.append({"name": upload.filename, "error": error})
        else:
            out.append(item)
    services.audit(user["id"], "excel.upload", "", f"{len(out)} files")
    return {"items": out}


@router.get("/excel/files")
def excel_files(request: Request):
    user = auth.current_user(request)
    rows = db.q("SELECT * FROM pf_attachments WHERE user_id=? AND "
                "kind IN ('xlsx','xls','csv') ORDER BY created_at DESC LIMIT 50",
                (user["id"],))
    return {"items": [pf_files.public(r) for r in rows], "total": len(rows)}


@router.get("/excel/profile")
def excel_profile(request: Request, file_id: str):
    user = auth.current_user(request)
    row = pf_files.get(file_id, user["id"])
    if not row:
        raise HTTPException(404, "文件不存在")
    try:
        return pf_excel.profile_public(file_id, row["path"])
    except Exception as exc:
        raise HTTPException(400, f"解析失败：{exc}")


@router.post("/excel/suggest")
def excel_suggest(request: Request, payload: dict = Body(default={})):
    user = auth.current_user(request)
    row = pf_files.get(payload.get("file_id") or "", user["id"])
    if not row:
        raise HTTPException(404, "文件不存在")
    try:
        return {"items": pf_excel.suggestions(row["id"], row["path"])}
    except Exception as exc:
        raise HTTPException(400, f"解析失败：{exc}")


@router.post("/excel/ask")
def excel_ask(request: Request, payload: dict = Body(default={})):
    user = auth.current_user(request)
    row = pf_files.get(payload.get("file_id") or "", user["id"])
    if not row:
        raise HTTPException(404, "文件不存在")
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    history = payload.get("history") or []
    if not isinstance(history, list):
        history = []
    events = queue.Queue()

    def stage(name, detail=""):
        events.put({"type": "stage", "name": name, "detail": detail})

    def worker():
        try:
            result = pf_excel.ask(row["id"], question, history[-5:], stage,
                                  kb_ids=payload.get("kb_ids") or [])
            if result.get("ok"):
                events.put({"type": "result", **result})
            else:
                events.put({"type": "error", "message": result.get("error")
                            or "问数失败", "code": result.get("code")})
        except Exception as exc:
            events.put({"type": "error",
                        "message": f"{type(exc).__name__}: {exc}"})
        finally:
            events.put({"type": "done"})

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        yield "data: " + json.dumps({"type": "start", "file_id": row["id"],
                                     "name": row["name"]},
                                    ensure_ascii=False) + "\n\n"
        while True:
            item = events.get()
            yield "data: " + json.dumps(item, ensure_ascii=False,
                                        default=str) + "\n\n"
            if item.get("type") == "done":
                break

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})



@router.get("/runtimes")
def runtimes(request: Request, force: bool = False):
    auth.current_user(request)
    return external.probe(force=force)


@router.post("/external/run")
def external_run(request: Request, payload: dict = Body(default={})):
    user = auth.current_user(request)
    kind = payload.get("remote_kind") or payload.get("kind") or "agent"
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "外部对象名称不能为空")
    if not external.configured():
        raise HTTPException(400, "未配置 ZDATA_BASE_URL")
    item = {"kind": kind, "remote_kind": kind, "name": name}
    from .api_catalog import sse
    services.audit(user["id"], "external.run", f"{kind}:{name}", "")
    return sse(external.stream_run(item, payload, user["id"]))



# ────────────────────────── shares / public pages ──────────────────────────

def _snapshot(kind, ref_id, payload, title):
    if kind == "session":
        sid = ref_id or ""
        if sid.startswith("se_"):
            session = db.one("SELECT * FROM pf_sessions WHERE id=?", (sid,))
            messages = db.q("SELECT role,content,created_at FROM pf_messages "
                            "WHERE session_id=? ORDER BY created_at", (sid,))
        else:
            session = db.one("SELECT * FROM sessions WHERE id=?", (sid,))
            messages = db.q("SELECT role,content,created_at FROM messages "
                            "WHERE session_id=? ORDER BY created_at", (sid,))
        if not session:
            raise HTTPException(404, "会话不存在")
        return (title or session.get("title") or "对话",
                {"messages": messages, "source": "session"})
    body = payload if isinstance(payload, dict) else {}
    messages = body.get("messages") or []
    return (title or body.get("title") or "分享内容",
            {"messages": messages, "source": "snapshot",
             "note": body.get("note") or ""})


def _share_hash(password):
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"), 120000).hex()
    return salt + "$" + digest


def _share_check(password_hash, password):
    if not password_hash:
        return True
    try:
        salt, digest = password_hash.split("$", 1)
    except ValueError:
        return False
    return hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                               salt.encode("utf-8"), 120000).hex() == digest


@router.post("/shares")
def create_share(request: Request, payload: dict = Body(default={})):
    user = auth.current_user(request)
    kind = payload.get("kind") or "snapshot"
    title, snapshot = _snapshot(kind, payload.get("ref_id") or "",
                                payload.get("payload") or {},
                                payload.get("title") or "")
    days = int(payload.get("days") or 0)
    expires = (dt.datetime.now() + dt.timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S") if days > 0 else ""
    password = str(payload.get("password") or "")
    sid = services.uid("sh_")
    token = secrets.token_urlsafe(12)
    db.x("INSERT INTO pf_shares(id,token,kind,ref_id,title,payload,"
         "password_hash,views,created_by,created_at,expires_at) "
         "VALUES(?,?,?,?,?,?,?,0,?,?,?)",
         (sid, token, kind, payload.get("ref_id") or "", title,
          services.jdump(snapshot),
          _share_hash(password) if password else "",
          user["id"], services.now(), expires))
    services.audit(user["id"], "share.create", sid, title)
    return {"id": sid, "token": token, "title": title,
            "url": f"/share/{token}", "expires_at": expires,
            "protected": bool(password)}


@router.get("/shares")
def list_shares(request: Request):
    user = auth.current_user(request)
    sql = "SELECT * FROM pf_shares"
    args = ()
    if user["role"] != "admin":
        sql += " WHERE created_by=?"
        args = (user["id"],)
    sql += " ORDER BY created_at DESC LIMIT 100"
    rows = db.q(sql, args)
    for row in rows:
        row["url"] = f"/share/{row['token']}"
    return {"items": rows, "total": len(rows)}


@router.delete("/shares/{share_id}")
def delete_share(request: Request, share_id: str):
    user = auth.require(request, "editor")
    db.x("DELETE FROM pf_shares WHERE id=?", (share_id,))
    services.audit(user["id"], "share.delete", share_id, "")
    return {"ok": True}


@router.get("/share/{token}")
def public_share(request: Request, token: str, password: str = ""):
    row = db.one("SELECT * FROM pf_shares WHERE token=?", (token,))
    if not row:
        raise HTTPException(404, "分享不存在")
    if row.get("expires_at") and row["expires_at"] < services.now():
        raise HTTPException(410, "分享已过期")
    supplied = (request.headers.get("X-Share-Password") or password or "")
    if not _share_check(row.get("password_hash"), supplied):
        raise HTTPException(401, "需要访问密码")
    db.x("UPDATE pf_shares SET views=COALESCE(views,0)+1 WHERE id=?",
         (row["id"],))
    return {"title": row["title"], "created_at": row["created_at"],
            "views": (row.get("views") or 0) + 1,
            "payload": services.jload(row["payload"], {})}


@router.get("/artifacts/{artifact_id}")
def artifact_meta(request: Request, artifact_id: str):
    auth.current_user(request)
    row = db.one("SELECT * FROM pf_artifacts WHERE id=?", (artifact_id,))
    if not row:
        raise HTTPException(404, "产物不存在")
    return row


@router.get("/export")
def export_platform(request: Request):
    """JSON export of authored platform objects (admin only)."""
    auth.require(request, "admin")
    tables = ("pf_users", "pf_agents", "pf_agent_versions", "pf_skills",
              "pf_experts", "pf_teams", "pf_workflows", "pf_memories",
              "pf_evals", "pf_schedules", "pf_settings")
    data = {"exported_at": services.now(), "version": config.VERSION,
            "tables": {}}
    for table in tables:
        rows = db.q(f"SELECT * FROM {table}")
        data["tables"][table] = rows
    services.audit(auth.current_user(request)["id"], "platform.export",
                   "platform", ",".join(tables))
    return data



@router.post("/maintenance/retention")
def retention(request: Request, payload: dict = Body(default={})):
    """Delete runs (and their steps/artifacts) older than N days."""
    user = auth.require(request, "admin")
    days = max(1, min(int(payload.get("days") or 90), 3650))
    cutoff = (dt.datetime.now() - dt.timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S")
    runs = db.q("SELECT id FROM pf_runs WHERE created_at < ?", (cutoff,))
    ids = [r["id"] for r in runs]
    artifacts = []
    removed_files = 0
    for run_id in ids:
        for art in db.q("SELECT path FROM pf_artifacts WHERE run_id=?", (run_id,)):
            path = art.get("path") or ""
            root = os.path.abspath(os.path.join(config.DATA, "platform_artifacts"))
            if path and os.path.abspath(path).startswith(root + os.sep)                     and os.path.exists(path):
                try:
                    os.remove(path)
                    removed_files += 1
                except OSError:
                    pass
        db.x("DELETE FROM pf_artifacts WHERE run_id=?", (run_id,))
        db.x("DELETE FROM pf_run_steps WHERE run_id=?", (run_id,))
        db.x("DELETE FROM pf_runs WHERE id=?", (run_id,))
    services.audit(user["id"], "maintenance.retention", "runs",
                   f"days={days} runs={len(ids)} files={removed_files}")
    return {"ok": True, "days": days, "runs": len(ids),
            "files": removed_files}


@router.get("/export/qa")
def export_qa(request: Request, space_id: str = "", limit: int = 5000):
    """CSV export of Q&A history for one space (or all spaces)."""
    auth.require(request, "editor")
    sql = ("SELECT question,answer,verdict,feedback,correction,created_at "
           "FROM qa_events")
    args = []
    if space_id:
        sql += " WHERE space_id=?"
        args.append(space_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 20000)))
    rows = db.q(sql, tuple(args))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["question", "answer", "verdict", "feedback",
                     "correction", "created_at"])
    for row in rows:
        writer.writerow([row["question"], row["answer"], row["verdict"],
                         row["feedback"], row["correction"], row["created_at"]])
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 "attachment; filename=knowlens-qa-history.csv"})



@router.get("/notifications")
def list_notifications(request: Request, unread: str = "", limit: int = 100):
    user = auth.current_user(request)
    sql = "SELECT * FROM pf_notifications WHERE user_id=?"
    args = [user["id"]]
    if unread in ("1", "true"):
        sql += " AND read=0"
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 300)))
    rows = db.q(sql, tuple(args))
    count = db.one("SELECT COUNT(*) c FROM pf_notifications WHERE user_id=? "
                   "AND read=0", (user["id"],))["c"]
    return {"items": rows, "total": len(rows), "unread": count}


@router.post("/notifications/{notification_id}/read")
def read_notification(request: Request, notification_id: str):
    user = auth.current_user(request)
    db.x("UPDATE pf_notifications SET read=1 WHERE id=? AND user_id=?",
         (notification_id, user["id"]))
    return {"ok": True}


@router.post("/notifications/read-all")
def read_all_notifications(request: Request):
    user = auth.current_user(request)
    db.x("UPDATE pf_notifications SET read=1 WHERE user_id=?", (user["id"],))
    return {"ok": True}


@router.post("/documents/bulk_tags")
def bulk_tags(request: Request, payload: dict = Body(default={})):
    """Add/remove tag names on many documents in one call."""
    user = auth.require(request, "editor")
    ids = [str(x) for x in (payload.get("document_ids") or [])][:500]
    add = services.parse_tags(payload.get("add"))
    remove = services.parse_tags(payload.get("remove"))
    if not ids or (not add and not remove):
        raise HTTPException(400, "需要文档与至少一个标签")
    marks = ",".join("?" for _ in ids)
    docs = db.q("SELECT id,space_id FROM documents WHERE id IN (%s)" % marks,
                tuple(ids))
    added = removed = 0
    now = services.now()
    for doc in docs:
        for name in add:
            tag = db.one("SELECT id FROM tags WHERE space_id=? AND name=?",
                         (doc["space_id"], name))
            if not tag:
                tid = services.uid("tg_")
                db.x("INSERT INTO tags(id,space_id,name,auto,created_at) "
                     "VALUES(?,?,?,0,?)", (tid, doc["space_id"], name, now))
            else:
                tid = tag["id"]
            exists = db.one("SELECT 1 x FROM doc_tags WHERE document_id=? AND "
                            "tag_id=?", (doc["id"], tid))
            if not exists:
                db.x("INSERT INTO doc_tags(document_id,tag_id) VALUES(?,?)",
                     (doc["id"], tid))
                added += 1
        for name in remove:
            tag = db.one("SELECT id FROM tags WHERE space_id=? AND name=?",
                         (doc["space_id"], name))
            if tag:
                cur = db.conn().execute(
                    "DELETE FROM doc_tags WHERE document_id=? AND tag_id=?",
                    (doc["id"], tag["id"]))
                removed += cur.rowcount
    db.conn().commit()
    services.audit(user["id"], "document.bulk_tags", f"{len(docs)} docs",
                   f"add={add} remove={remove}")
    return {"ok": True, "documents": len(docs), "added": added,
            "removed": removed}



@router.post("/ingest/preview")
def ingest_preview(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    try:
        page = webingest.fetch_page(payload.get("url") or "")
    except Exception as exc:
        raise HTTPException(400, f"抓取失败: {exc}")
    services.audit(user["id"], "document.preview_url", "",
                   payload.get("url") or "")
    return {"title": page["title"], "url": page["url"],
            "chars": len(page["markdown"]),
            "markdown": page["markdown"][:6000]}


@router.post("/ingest/url")
def ingest_url(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    result, error = webingest.ingest_url(
        payload.get("space_id") or "", payload.get("library_id") or "",
        payload.get("url") or "", payload.get("tags") or [], user["id"])
    if error:
        raise HTTPException(400, error)
    return result


@router.get("/settings")
def get_settings(request: Request):
    user = auth.current_user(request)
    keys = ("platform.name", "platform.theme", "platform.default_agent",
            "platform.model", "platform.locale")
    values = {k: services.setting_get(k) for k in keys}
    return {"values": values, "user": user,
            "channels": llm_client.available_channels(),
            "stub": llm_client.stub_mode(),
            "roles": list(auth.ROLE_RANK)}


@router.put("/settings")
def put_settings(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "admin")
    values = payload.get("values") or payload
    for key, value in values.items():
        if not str(key).startswith("platform."):
            continue
        services.setting_set(key, value)
    services.audit(user["id"], "settings.update", "platform",
                   ",".join(values))
    return get_settings(request)