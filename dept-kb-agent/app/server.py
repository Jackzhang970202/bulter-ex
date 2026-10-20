# -*- coding: utf-8 -*-
"""FastAPI 服务：REST + SSE（问答轨迹实时流）+ 静态前端。

隔离强约束：
- 问答范围参数只接受 doc_id/库 id，且服务端按 space_id 过滤后构造白名单；
- adhoc 会话不能引用空间正式文档（除非用户显式“参考库”并选择，同一空间内）。
"""
import asyncio
import json
import os
import shutil
import threading
import time

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, evolution, ingest, kb, llm, qa

app = FastAPI(title="dept-kb-agent")

WEB = os.path.join(config.ROOT, "web")


# ────────────────────────── 基础 ──────────────────────────

@app.get("/api/status")
def status():
    ch = llm.current_channel()
    return {"ok": True, "channel": llm.channel(), "protocol": ch["protocol"],
            "model": ch["model"],
            "spaces": db.one("SELECT COUNT(*) c FROM spaces")["c"],
            "docs_ready": db.one(
                "SELECT COUNT(*) c FROM documents WHERE status='ready'")["c"],
            "ffmpeg": bool(ingest.audio.ffmpeg_path())}


def _space(sid):
    s = db.one("SELECT * FROM spaces WHERE id=?", (sid,))
    if not s:
        raise HTTPException(404, "空间不存在")
    return s


# ────────────────────────── 空间 ──────────────────────────

@app.get("/api/spaces")
def list_spaces():
    out = []
    for s in db.q("SELECT * FROM spaces ORDER BY created_at"):
        s["lib_count"] = db.one(
            "SELECT COUNT(*) c FROM libraries WHERE space_id=?",
            (s["id"],))["c"]
        s["doc_count"] = db.one(
            "SELECT COUNT(*) c FROM documents WHERE space_id=? "
            "AND status='ready'", (s["id"],))["c"]
        d = evolution.current_digest(s["id"])
        s["digest_ver"] = d["ver"] if d else 0
        out.append(s)
    return out


@app.post("/api/spaces")
async def create_space(req: Request):
    b = await req.json()
    name = (b.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "空间名必填")
    sid = db.new_id("sp-")
    db.x("INSERT INTO spaces(id,name,description,created_at) VALUES(?,?,?,?)",
         (sid, name, b.get("description", ""), db.now()))
    os.makedirs(os.path.join(config.SPACES, sid), exist_ok=True)
    evolution.render(sid)
    return {"id": sid, "name": name}


@app.delete("/api/spaces/{sid}")
def delete_space(sid: str):
    _space(sid)
    for r in db.q("SELECT source_path FROM documents WHERE space_id=?", (sid,)):
        p = r["source_path"]
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    db.x("DELETE FROM spaces WHERE id=?", (sid,))
    shutil.rmtree(os.path.join(config.SPACES, sid), ignore_errors=True)
    return {"ok": True}


# ────────────────────────── 库 ──────────────────────────

@app.get("/api/spaces/{sid}/libraries")
def list_libs(sid: str):
    _space(sid)
    libs = db.q("SELECT * FROM libraries WHERE space_id=? ORDER BY created_at",
                (sid,))
    for l in libs:
        l["doc_count"] = db.one(
            "SELECT COUNT(*) c FROM documents WHERE library_id=? "
            "AND status='ready'", (l["id"],))["c"]
        l["pending_count"] = db.one(
            "SELECT COUNT(*) c FROM documents WHERE library_id=? "
            "AND status IN ('pending','indexing')", (l["id"],))["c"]
        l["failed_count"] = db.one(
            "SELECT COUNT(*) c FROM documents WHERE library_id=? "
            "AND status='failed'", (l["id"],))["c"]
    return libs


@app.post("/api/spaces/{sid}/libraries")
async def create_lib(sid: str, req: Request):
    _space(sid)
    b = await req.json()
    name = (b.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "库名必填")
    lid = db.new_id("lb-")
    db.x("INSERT INTO libraries(id,space_id,name,description,created_at) "
         "VALUES(?,?,?,?,?)", (lid, sid, name, b.get("description", ""),
                               db.now()))
    return {"id": lid}


@app.patch("/api/libraries/{lid}")
async def update_lib(lid: str, req: Request):
    b = await req.json()
    l = db.one("SELECT * FROM libraries WHERE id=?", (lid,))
    if not l:
        raise HTTPException(404)
    db.x("UPDATE libraries SET name=?, description=? WHERE id=?",
         (b.get("name", l["name"]), b.get("description", l["description"]), lid))
    return {"ok": True}


@app.delete("/api/libraries/{lid}")
def delete_lib(lid: str):
    l = db.one("SELECT * FROM libraries WHERE id=?", (lid,))
    if not l:
        raise HTTPException(404)
    for r in db.q("SELECT doc_id FROM documents WHERE library_id=? AND doc_id "
                  "IS NOT NULL", (lid,)):
        try:
            kb.delete_doc(l["space_id"], r["doc_id"])
        except Exception:
            pass
        for d in db.q("SELECT source_path FROM documents WHERE library_id=?",
                      (lid,)):
            if d["source_path"] and os.path.isfile(d["source_path"]):
                try:
                    os.remove(d["source_path"])
                except OSError:
                    pass
    db.x("DELETE FROM libraries WHERE id=?", (lid,))
    evolution.render(l["space_id"])
    return {"ok": True}


# ────────────────────────── 标签 ──────────────────────────

@app.get("/api/spaces/{sid}/tags")
def list_tags(sid: str):
    return db.q("SELECT * FROM tags WHERE space_id=? ORDER BY auto, name",
                (sid,))


@app.post("/api/spaces/{sid}/tags")
async def add_tag(sid: str, req: Request):
    b = await req.json()
    name = (b.get("name") or "").strip()[:24]
    if not name:
        raise HTTPException(400)
    tid = db.one("SELECT id FROM tags WHERE space_id=? AND name=?", (sid, name))
    if not tid:
        tid = {"id": db.new_id("tg-")}
        db.x("INSERT INTO tags(id,space_id,name,auto,created_at) VALUES(?,?,?,0,?)",
             (tid["id"], sid, name, db.now()))
    else:
        tid = {"id": tid["id"]}
    return tid


@app.delete("/api/tags/{tid}")
def del_tag(tid: str):
    db.x("DELETE FROM tags WHERE id=?", (tid,))
    return {"ok": True}


# ────────────────────────── 文档 ──────────────────────────

@app.get("/api/libraries/{lid}/documents")
def list_docs(lid: str):
    docs = db.q(
        "SELECT d.*, (SELECT group_concat(t.name) FROM doc_tags dt "
        "JOIN tags t ON t.id=dt.tag_id WHERE dt.document_id=d.id) AS tag_names "
        "FROM documents d WHERE d.library_id=? ORDER BY d.created_at DESC",
        (lid,))
    return docs


@app.post("/api/documents/upload")
async def upload(space_id: str = Form(...), library_id: str = Form(...),
                 files: list[UploadFile] = File(...),
                 tag_ids: str = Form("[]")):
    _space(space_id)
    lib = db.one("SELECT * FROM libraries WHERE id=? AND space_id=?",
                 (library_id, space_id))
    if not lib:
        raise HTTPException(400, "库不属于该空间")
    wanted = json.loads(tag_ids or "[]")
    ids = []
    for f in files:
        name = os.path.basename(f.filename or "unnamed")
        kind = ingest.kind_of(name)
        if kind == "unknown":
            raise HTTPException(400, f"不支持的类型: {name}")
        did = db.new_id("dc-")
        updir = os.path.join(config.UPLOADS, space_id, did)
        os.makedirs(updir, exist_ok=True)
        path = os.path.join(updir, name)
        with open(path, "wb") as out:
            shutil.copyfileobj(f.file, out)
        db.x("INSERT INTO documents(id,space_id,library_id,name,kind,"
             "status,source_path,created_at,updated_at) "
             "VALUES(?,?,?,?,?,'pending',?,?,?)",
             (did, space_id, library_id, name, kind, path, db.now(), db.now()))
        for tid in wanted:
            db.x("INSERT OR IGNORE INTO doc_tags(document_id,tag_id) "
                 "VALUES(?,?)", (did, tid))
        ids.append(did)
    for did in ids:
        ingest.start_job(did)
    return {"document_ids": ids}


@app.post("/api/documents/{did}/reindex")
def reindex(did: str):
    d = db.one("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        raise HTTPException(404)
    if d["doc_id"]:
        try:
            kb.delete_doc(d["space_id"], d["doc_id"])
        except Exception:
            pass
    db.x("UPDATE documents SET status='pending', doc_id=NULL, error='' "
         "WHERE id=?", (did,))
    ingest.start_job(did)
    return {"ok": True}


@app.delete("/api/documents/{did}")
def delete_doc(did: str):
    d = db.one("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        raise HTTPException(404)
    if d["doc_id"]:
        try:
            kb.delete_doc(d["space_id"], d["doc_id"])
        except Exception:
            pass
    db.x("DELETE FROM documents WHERE id=?", (did,))
    if d["source_path"] and os.path.isfile(d["source_path"]):
        try:
            os.remove(d["source_path"])
        except OSError:
            pass
    threading.Thread(target=evolution.render, args=(d["space_id"],),
                     daemon=True).start()
    return {"ok": True}


@app.post("/api/documents/{did}/tags")
async def set_doc_tags(did: str, req: Request):
    d = db.one("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        raise HTTPException(404)
    b = await req.json()
    tids = [t for t in (b.get("tag_ids") or []) if db.one(
        "SELECT id FROM tags WHERE id=? AND space_id=?", (t, d["space_id"]))]
    db.x("DELETE FROM doc_tags WHERE document_id=?", (did,))
    for t in tids:
        db.x("INSERT OR IGNORE INTO doc_tags(document_id,tag_id) VALUES(?,?)",
             (did, t))
    return {"ok": True, "tag_ids": tids}


@app.get("/api/documents/{did}/tree")
def doc_tree(did: str):
    d = db.one("SELECT * FROM documents WHERE id=?", (did,))
    if not d or not d["doc_id"]:
        raise HTTPException(404, "文档未就绪")
    return kb.structure_tree(d["space_id"], d["doc_id"]) or []


@app.get("/api/documents/{did}/pages/{page}")
def doc_page(did: str, page: int):
    d = db.one("SELECT * FROM documents WHERE id=?", (did,))
    if not d or not d["doc_id"]:
        raise HTTPException(404)
    pg = kb.get_pages(d["space_id"], d["doc_id"], [page])
    if not pg:
        raise HTTPException(404, "页不存在")
    return pg[0]


@app.get("/api/documents/{did}/download")
def doc_download(did: str):
    d = db.one("SELECT * FROM documents WHERE id=?", (did,))
    if not d or not os.path.isfile(d["source_path"] or ""):
        raise HTTPException(404)
    return FileResponse(d["source_path"], filename=d["name"])


# ────────────────────────── 进度事件轮询 ──────────────────────────

@app.get("/api/events")
def events(space_id: str, since: int = 0):
    rows = db.q("SELECT * FROM events WHERE space_id=? AND id>?"
                " ORDER BY id LIMIT 200", (space_id, since))
    for r in rows:
        r["payload"] = json.loads(r["payload"] or "{}")
    return {"events": rows, "last": rows[-1]["id"] if rows else since}


# ────────────────────────── 临时上传（adhoc 窗口） ──────────────────────────

@app.post("/api/adhoc/upload")
async def adhoc_upload(request: Request):
    form = await request.form()
    space_id = form.get("space_id", "")
    _space(space_id)
    session_id = form.get("session_id", "")
    out = []
    for f in form.getlist("files"):
        name = os.path.basename(f.filename or "unnamed")
        kind = ingest.kind_of(name)
        aid = db.new_id("ad-")
        updir = os.path.join(config.UPLOADS, space_id, "adhoc", aid)
        os.makedirs(updir, exist_ok=True)
        path = os.path.join(updir, name)
        with open(path, "wb") as o:
            shutil.copyfileobj(f.file, o)
        try:
            rec = _adhoc_to_material(space_id, aid, name, kind, path)
        except Exception as e:
            db.x("INSERT INTO adhoc_files(id,space_id,name,kind,path,session_id,"
                 "created_at) VALUES(?,?,?,?,?,?,?)",
                 (aid, space_id, name, kind, path, session_id, db.now()))
            out.append({"id": aid, "name": name, "error": str(e)[:200]})
            continue
        rec["id"] = aid
        rec["name"] = name
        out.append(rec)
    return {"files": out}


def _adhoc_to_material(space_id, aid, name, kind, path):
    """转 markdown；小文件内联存 md 列，大文件索引进空间 PI store。"""
    if kind == "pdf":
        doc_id, pages, desc = kb.submit_pdf(space_id, path)
        db.x("INSERT INTO adhoc_files(id,space_id,name,kind,path,doc_id,chars,"
             "session_id,created_at) VALUES(?,?,?,?,?,?,?,? ,?)",
             (aid, space_id, name, kind, path, doc_id, 0, "", db.now()))
        return {"mode": "indexed", "doc_id": doc_id, "pages": pages}
    if kind == "docx":
        md = ingest.docx_to_md(path)
    elif kind == "pptx":
        md = ingest.pptx_to_md(path)
    elif kind in ("xlsx", "xls"):
        md = ingest.xlsx_to_md(path)
    elif kind == "audio":
        md = ingest.audio.transcribe_to_minutes_md(
            path, name, lambda d: db.log_event(space_id, aid, "progress",
                                               {"stage": "asr", "detail": d}))
    elif kind in ("md", "txt"):
        md = open(path, encoding="utf-8", errors="replace").read()
    elif kind == "image":
        md = ingest.vision_ocr_image(open(path, "rb").read())
    else:
        md = open(path, encoding="utf-8", errors="replace").read()
    if len(md) <= ingest.ADHOC_INLINE_MAX:
        db.x("INSERT INTO adhoc_files(id,space_id,name,kind,path,chars,md,"
             "session_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
             (aid, space_id, name, kind, path, len(md), md, "", db.now()))
        return {"mode": "inline", "chars": len(md)}
    tree, pages, desc = ingest.md_to_pi(md, name)
    doc_id = kb.seed_doc(space_id, "临席:" + name, desc, tree, pages,
                         {"adhoc": aid, "title": name})
    db.x("INSERT INTO adhoc_files(id,space_id,name,kind,path,doc_id,chars,"
         "session_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
         (aid, space_id, name, kind, path, doc_id, len(md), "", db.now()))
    return {"mode": "indexed", "doc_id": doc_id, "pages": len(pages)}


@app.delete("/api/adhoc/{aid}")
def adhoc_delete(aid: str):
    r = db.one("SELECT * FROM adhoc_files WHERE id=?", (aid,))
    if not r:
        raise HTTPException(404)
    if r["doc_id"]:
        try:
            kb.delete_doc(r["space_id"], r["doc_id"])
        except Exception:
            pass
    db.x("DELETE FROM adhoc_files WHERE id=?", (aid,))
    if r["path"] and os.path.isfile(r["path"]):
        try:
            os.remove(r["path"])
        except OSError:
            pass
    return {"ok": True}


# ────────────────────────── 会话 ──────────────────────────

@app.get("/api/spaces/{sid}/sessions")
def list_sessions(sid: str, mode: str = ""):
    if mode:
        rows = db.q("SELECT * FROM sessions WHERE space_id=? AND mode=?"
                    " ORDER BY updated_at DESC", (sid, mode))
    else:
        rows = db.q("SELECT * FROM sessions WHERE space_id=?"
                    " ORDER BY updated_at DESC", (sid,))
    return rows


@app.get("/api/sessions/{sid_}")
def get_session(sid_: str):
    s = db.one("SELECT * FROM sessions WHERE id=?", (sid_,))
    if not s:
        raise HTTPException(404)
    msgs = db.q("SELECT * FROM messages WHERE session_id=? ORDER BY created_at",
                (sid_,))
    for m in msgs:
        m["meta"] = json.loads(m["meta"] or "{}")
    s["messages"] = msgs
    return s


@app.delete("/api/sessions/{sid_}")
def del_session(sid_: str):
    db.x("DELETE FROM sessions WHERE id=?", (sid_,))
    return {"ok": True}


# ────────────────────────── 问答（SSE） ──────────────────────────

@app.post("/api/qa/stream")
async def qa_stream(req: Request):
    b = await req.json()
    space_id = b.get("space_id")
    _space(space_id)
    question = (b.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "问题为空")
    mode = b.get("mode", "kb")
    session_id = b.get("session_id") or db.new_id("ss-")
    library_ids = b.get("library_ids") or []
    tag_ids = b.get("tag_ids") or []
    adhoc_ids = b.get("adhoc_ids") or []
    ref_libs = b.get("ref_library_ids") or []

    s = db.one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not s:
        db.x("INSERT INTO sessions(id,space_id,mode,title,context,scope,"
             "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
             (session_id, space_id, mode, question[:30], "[]",
              json.dumps({"library_ids": library_ids, "tag_ids": tag_ids,
                          "adhoc_ids": adhoc_ids}, ensure_ascii=False),
              db.now(), db.now()))
        history = []
    else:
        if s["space_id"] != space_id or s["mode"] != mode:
            raise HTTPException(400, "会话与空间/模式不匹配")
        scope = json.loads(s["scope"] or "{}")
        history = db.q("SELECT role, content FROM messages WHERE session_id=?"
                       " ORDER BY created_at DESC LIMIT 12", (session_id,))
        history = list(reversed(history))
        if mode == "kb":
            old_libs = set(scope.get("library_ids") or [])
            if old_libs and set(library_ids) != old_libs:
                history = []   # 检索范围变更 → 上下文重置（提示前端）
        db.x("UPDATE sessions SET scope=?, updated_at=? WHERE id=?",
             (json.dumps({"library_ids": library_ids, "tag_ids": tag_ids,
                          "adhoc_ids": adhoc_ids}, ensure_ascii=False),
              db.now(), session_id))

    mid = db.new_id("ms-")
    db.x("INSERT INTO messages(id,session_id,role,content,created_at) "
         "VALUES(?,?, 'user', ?, ?)", (mid, session_id, question, db.now()))

    temp_context = ""
    temp_docs = None
    if mode == "adhoc":
        rows = db.q("SELECT * FROM adhoc_files WHERE space_id=? "
                    "AND id IN (%s)" % ",".join("?" * len(adhoc_ids)),
                    tuple([space_id] + adhoc_ids))
        inline_texts = []
        doc_ids = []
        for r in rows:
            if r["doc_id"]:
                metas = {m["id"]: m for m in kb.list_metas(space_id)}
                m = metas.get(r["doc_id"])
                if m:
                    doc_ids.append(r["doc_id"])
                    temp_docs = (temp_docs or []) + [
                        {"doc_id": r["doc_id"], "name": m.get("name", r["name"]),
                         "library_id": "adhoc", "description":
                         (m.get("description") or "")[:200], "tags": []}]
            elif r["md"]:
                inline_texts.append(f"【{r['name']}】\n{r['md']}")
        temp_context = "\n\n".join(inline_texts)[:60000]
        if ref_libs:
            kb_docs = qa.candidate_docs(space_id, ref_libs, [])
            temp_docs = (temp_docs or []) + kb_docs
        if not temp_docs and not temp_context:
            def _no_material():
                yield _sse({"type": "result", "verdict": "未覆盖",
                            "answer": "请先上传临时文件，或在会话中保留文件后再提问。",
                            "citations": [], "session_id": session_id})
            return StreamingResponse(_no_material(),
                                     media_type="text/event-stream")
        space_id_for_chat = space_id
    else:
        space_id_for_chat = space_id

    digest = evolution.get_digest_text(space_id) if mode == "kb" else ""

    def gen():
        events_collected = []
        result_box = {}
        err_box = {}

        def on_ev(e):
            events_collected.append(e)
            q.put(e)

        import queue
        q = queue.Queue()

        def worker():
            try:
                if mode == "adhoc":
                    res = qa.answer(space_id_for_chat, question,
                                    mode="adhoc", temp_context=temp_context,
                                    temp_docs=temp_docs or None,
                                    history=history, digest=digest,
                                    on_event=on_ev)
                else:
                    res = qa.answer(space_id_for_chat, question,
                                    library_ids=library_ids, tag_ids=tag_ids,
                                    mode="kb", history=history, digest=digest,
                                    on_event=on_ev)
                result_box.update(res or {})
            except Exception as e:
                import traceback
                traceback.print_exc()
                err_box["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            finally:
                q.put(None)

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        yield _sse({"type": "start", "session_id": session_id,
                    "message_id": mid})
        while True:
            try:
                e = q.get(timeout=20)
            except queue.Empty:
                yield _sse({"type": "ping"})
                continue
            if e is None:
                break
            yield _sse(e)
        if err_box:
            yield _sse({"type": "error", "message": err_box.get("error", "")})
            answer_text = "（回答失败：" + err_box.get("error", "") + "）"
            verdict = "未覆盖"
            citations = []
        else:
            answer_text = result_box.get("answer", "")
            verdict = result_box.get("verdict", "")
            citations = result_box.get("citations", [])
        ev_id = db.new_id("qa-")
        db.x("INSERT INTO qa_events(id,session_id,space_id,question,answer,"
             "verdict,citations,created_at) VALUES(?,?,?,?,?,?,?,?)",
             (ev_id, session_id, space_id, question, answer_text[:8000],
              verdict, json.dumps(citations, ensure_ascii=False), db.now()))
        meta = {"citations": citations, "verdict": verdict,
                "qa_event": ev_id,
                "route": next((e for e in events_collected
                               if e["type"] == "route"), None),
                "tools": [e["name"] for e in events_collected
                          if e["type"] == "tool_call"]}
        db.x("INSERT INTO messages(id,session_id,role,content,meta,"
             "created_at) VALUES(?,?, 'assistant', ?, ?, ?)",
             (db.new_id("ms-"), session_id, answer_text[:12000],
              json.dumps(meta, ensure_ascii=False), db.now()))
        yield _sse({"type": "done", "qa_event": ev_id,
                    "session_id": session_id})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


# ────────────────────────── 反馈与演进 ──────────────────────────

@app.post("/api/feedback")
async def feedback(req: Request):
    b = await req.json()
    r = evolution.record_feedback(b.get("event_id"), b.get("kind", ""),
                                  b.get("correction", ""))
    if b.get("kind") in ("fix", "ok"):
        threading.Thread(target=_distill_after_feedback,
                         args=(b.get("event_id"),), daemon=True).start()
    return r or {"ok": True}


_distill_running = set()
_distill_lock = threading.Lock()


def _distill_worker(sid):
    def cb(stage, detail):
        db.log_event(sid, None, "distill_progress", {"stage": stage,
                                                     "detail": detail})
    try:
        r = evolution.distill_cognitive(sid, progress=cb)
        cb("done", (f"蒸馏完成，生成 digest v{r['ver']}（{r['bytes']}B）"
                    if r else "蒸馏完成（digest 超体积红线，保留旧版）"))
    except Exception as e:
        cb("failed", f"{type(e).__name__}: {str(e)[:160]}")
    finally:
        with _distill_lock:
            _distill_running.discard(sid)


def _start_distill(sid):
    """后台启动一次蒸馏；已在运行则复用。返回是否新启动。"""
    with _distill_lock:
        if sid in _distill_running:
            return False
        _distill_running.add(sid)
    threading.Thread(target=_distill_worker, args=(sid,), daemon=True).start()
    return True


@app.post("/api/spaces/{sid}/distill")
def distill(sid: str):
    _space(sid)
    started = _start_distill(sid)
    return {"started": started, "running": True}


def _distill_after_feedback(event_id):
    ev = db.one("SELECT * FROM qa_events WHERE id=?", (event_id,))
    if not ev:
        return
    n_pending = db.one(
        "SELECT COUNT(*) c FROM candidates WHERE space_id=? AND "
        "status='pending'", (ev["space_id"],))["c"]
    if n_pending and n_pending % 5 == 0:
        _start_distill(ev["space_id"])


@app.get("/api/spaces/{sid}/candidates")
def candidates(sid: str, status_: str = "pending"):
    rows = db.q("SELECT * FROM candidates WHERE space_id=? AND status=? "
                "ORDER BY updated_at DESC", (sid, status_))
    for r in rows:
        r["evidence"] = json.loads(r["evidence"] or "[]")
    return rows


@app.post("/api/candidates/{cid}/approve")
async def approve(cid: str, req: Request):
    try:
        b = await req.json()
    except Exception:
        b = {}
    if b.get("statement"):
        r = evolution.edit_approve(cid, b["statement"], b.get("ex_bad", ""),
                                   b.get("ex_good", ""))
    else:
        r = evolution.approve_candidate(cid)
    row = db.one("SELECT space_id FROM candidates WHERE id=?", (cid,))
    if row:
        evolution.render(row["space_id"])
    return r or {"ok": True}


@app.post("/api/candidates/{cid}/reject")
def reject(cid: str):
    evolution.reject_candidate(cid)
    return {"ok": True}


@app.get("/api/spaces/{sid}/digest")
def digest(sid: str):
    cur = evolution.current_digest(sid)
    versions = db.q("SELECT id,ver,bytes,nonce,active,created_at FROM digests "
                    "WHERE space_id=? ORDER BY ver DESC", (sid,))
    return {"current": cur, "versions": versions}


@app.post("/api/spaces/{sid}/digest/rollback")
async def digest_rollback(sid: str, req: Request):
    b = await req.json()
    evolution.rollback(sid, b.get("digest_id", ""))
    return {"ok": True}


@app.get("/api/spaces/{sid}/rules")
def rules(sid: str):
    return db.q("SELECT * FROM rules WHERE space_id=? ORDER BY id", (sid,))


@app.delete("/api/rules/{rid}")
def del_rule(rid: int, space_id: str):
    db.x("DELETE FROM rules WHERE id=? AND space_id=?", (rid, space_id))
    evolution.render(space_id)
    return {"ok": True}


@app.post("/api/spaces/{sid}/rules")
async def add_rule(sid: str, req: Request):
    b = await req.json()
    st = (b.get("statement") or "").strip()
    if not st:
        raise HTTPException(400)
    db.x("INSERT INTO rules(space_id,statement,ex_bad,ex_good,status,source,"
         "created_at) VALUES(?,?,?,?,'active','manual',?)",
         (sid, st[:600], b.get("ex_bad", ""), b.get("ex_good", ""), db.now()))
    evolution.render(sid)
    return {"ok": True}


@app.get("/api/spaces/{sid}/qa_history")
def qa_history(sid: str):
    return db.q("SELECT id,question,verdict,feedback,created_at FROM qa_events "
                "WHERE space_id=? ORDER BY created_at DESC LIMIT 100", (sid,))


# ────────────────────────── 静态前端 ──────────────────────────

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


app.mount("/static", StaticFiles(directory=WEB), name="static")


def run():
    import uvicorn
    db.init()
    os.makedirs(config.DATA, exist_ok=True)
    llm.init()
    uvicorn.run(app, host="127.0.0.1", port=config.PORT, log_level="warning")
