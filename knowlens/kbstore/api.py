# -*- coding: utf-8 -*-
"""Unified knowledge base API (``/api/kbs``)."""
import json

from fastapi import (APIRouter, Body, File, Form, HTTPException, Request,
                     UploadFile)

from .. import db
from ..platform import auth, services
from . import ingest, retrieval

router = APIRouter(prefix="/api/kbs", tags=["kbs"])


UNASSIGNED_SPACE = "sp_unassigned"


def _ensure_space(space_id):
    """Knowledge bases may live outside a workspace."""
    if space_id:
        return space_id
    if not db.one("SELECT id FROM spaces WHERE id=?", (UNASSIGNED_SPACE,)):
        db.x("INSERT OR IGNORE INTO spaces(id,name,description,created_at) "
             "VALUES(?,?,?,?)",
             (UNASSIGNED_SPACE, "未分组", "不属于任何空间的知识库",
              services.now()))
    return UNASSIGNED_SPACE


def _kb_or_404(kb_id):
    row = db.one("SELECT * FROM libraries WHERE id=?", (kb_id,))
    if not row:
        raise HTTPException(404, "知识库不存在")
    row["config"] = services.jload(row.get("config"), {})
    return row


def _public_kb(row):
    data = dict(row)
    data["config"] = services.jload(row.get("config"), {})
    stats = ingest.stats(row["id"]) or {}
    data["documents"] = stats.get("documents", 0)
    data["chunks"] = stats.get("chunks", 0)
    data["processing"] = stats.get("processing", 0)
    return data


@router.get("")
def list_kbs(request: Request, space_id: str = ""):
    auth.current_user(request)
    if space_id:
        rows = db.q("SELECT * FROM libraries WHERE space_id=? "
                    "ORDER BY created_at DESC", (space_id,))
    else:
        rows = db.q("SELECT * FROM libraries ORDER BY created_at DESC")
    return {"items": [_public_kb(row) for row in rows], "total": len(rows)}


@router.post("")
def create_kb(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "知识库名称不能为空")
    space_id = (payload.get("space_id") or "").strip()
    if space_id and not db.one("SELECT id FROM spaces WHERE id=?", (space_id,)):
        raise HTTPException(400, "空间不存在")
    space_id = _ensure_space(space_id)
    kb_id = services.uid("kb_")
    db.x("INSERT INTO libraries(id,space_id,name,description,summary,"
         "created_at,type,config,status,creator_id) "
         "VALUES(?,?,?,?,?,?,?,?,?,?)",
         (kb_id, space_id, name, payload.get("description") or "", "",
          services.now(), payload.get("type") or "document",
          json.dumps(payload.get("config") or {}, ensure_ascii=False),
          "active", user["id"]))
    services.audit(user["id"], "kb.create", kb_id, name)
    return _public_kb(_kb_or_404(kb_id))


@router.get("/{kb_id}")
def get_kb(request: Request, kb_id: str):
    auth.current_user(request)
    base = _kb_or_404(kb_id)
    stat = dict(ingest.stats(kb_id) or {})
    stat.pop("base", None)
    return {"base": base, **stat}


@router.patch("/{kb_id}")
def update_kb(request: Request, kb_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    _kb_or_404(kb_id)
    sets, args = [], []
    for key in ("name", "description", "status", "type"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(str(payload[key]))
    if "config" in payload:
        sets.append("config=?")
        args.append(json.dumps(payload.get("config") or {},
                               ensure_ascii=False))
    if not sets:
        return _public_kb(_kb_or_404(kb_id))
    args.append(kb_id)
    db.x(f"UPDATE libraries SET {','.join(sets)} WHERE id=?", args)
    services.audit(user["id"], "kb.update", kb_id, ",".join(payload.keys()))
    return _public_kb(_kb_or_404(kb_id))


@router.delete("/{kb_id}")
def delete_kb(request: Request, kb_id: str):
    user = auth.require(request, "admin")
    _kb_or_404(kb_id)
    for doc in ingest.list_documents(kb_id):
        ingest.delete_document(doc["id"], purge=True)
    db.x("DELETE FROM libraries WHERE id=?", (kb_id,))
    services.audit(user["id"], "kb.delete", kb_id, "")
    return {"ok": True}


@router.get("/{kb_id}/documents")
def list_documents(request: Request, kb_id: str, status: str = ""):
    auth.current_user(request)
    _kb_or_404(kb_id)
    return {"items": ingest.list_documents(kb_id, status)}


@router.post("/{kb_id}/documents")
async def upload(request: Request, kb_id: str, files: list[UploadFile] = File(...),
                 parse_mode: str = Form("chunk"),
                 parse_config: str = Form("{}"),
                 tags: str = Form("[]"),
                 validity_mode: str = Form("inherit"),
                 valid_from: str = Form(""), expires_at: str = Form(""),
                 duplicate_policy: str = Form("reject"),
                 relative_paths: str = Form("[]")):
    user = auth.require(request, "editor")
    _kb_or_404(kb_id)
    try:
        config = json.loads(parse_config or "{}")
    except Exception:
        raise HTTPException(400, "parse_config 必须是 JSON")
    try:
        tag_list = json.loads(tags or "[]")
    except Exception:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    out = []
    try:
        rel_paths = json.loads(relative_paths or "[]")
        if not isinstance(rel_paths, list):
            rel_paths = []
    except Exception:
        rel_paths = []
    for index, upload_file in enumerate(files[:10]):
        data = await upload_file.read()
        relative = str(rel_paths[index]) if index < len(rel_paths) else ""
        folder = relative.rsplit("/", 1)[0] if "/" in relative else ""
        row, error = ingest.add_document(
            kb_id, upload_file.filename, data, user["id"],
            parse_mode=parse_mode, parse_config=config, tags=tag_list,
            validity_mode=validity_mode, valid_from=valid_from,
            expires_at=expires_at, duplicate_policy=duplicate_policy,
            folder_path=folder)
        if error:
            out.append({"name": upload_file.filename, "error": error})
        else:
            out.append(row)
    services.audit(user["id"], "kb.upload", kb_id, f"{len(out)} files")
    return {"items": out}


@router.get("/documents/{doc_id}")
def get_document(request: Request, doc_id: str):
    auth.current_user(request)
    row = ingest.get_document(doc_id)
    if not row:
        raise HTTPException(404, "文档不存在")
    return row


@router.patch("/documents/{doc_id}")
def update_document(request: Request, doc_id: str,
                    payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = ingest.update_document(
        doc_id, tags=payload.get("tags"), enabled=payload.get("enabled"),
        valid_from=payload.get("valid_from"),
        expires_at=payload.get("expires_at"),
        validity_mode=payload.get("validity_mode"))
    if not row:
        raise HTTPException(404, "文档不存在")
    services.audit(user["id"], "kb.doc.update", doc_id,
                   ",".join(payload.keys()))
    return row


@router.delete("/documents/{doc_id}")
def delete_document(request: Request, doc_id: str, purge: bool = False):
    user = auth.require(request, "editor")
    if purge:
        ok = ingest.delete_document(doc_id, purge=True)
    else:
        ok = ingest.soft_delete_document(doc_id, user["id"])
    if not ok:
        raise HTTPException(404, "文档不存在")
    services.audit(user["id"], "kb.doc.delete", doc_id,
                   "purge" if purge else "recycle")
    return {"ok": True}


@router.post("/{kb_id}/documents/manual")
def create_manual(request: Request, kb_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    _kb_or_404(kb_id)
    title = (payload.get("title") or "").strip()
    content = (payload.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(400, "标题与正文不能为空")
    if len(content) > 200000:
        raise HTTPException(400, "正文超过 200000 字")
    row, error = ingest.add_document(
        kb_id, title + ".md", content.encode("utf-8"), user["id"],
        tags=payload.get("tags") or [], channel="manual",
        parse_mode="chunk", background=True)
    if error:
        raise HTTPException(400, error)
    services.audit(user["id"], "kb.doc.manual", row["id"], title[:80])
    return row


@router.post("/{kb_id}/documents/url")
def create_url(request: Request, kb_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    _kb_or_404(kb_id)
    url = (payload.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "只支持 http/https 链接")
    from ..platform import webingest
    try:
        page = webingest.fetch_page(url)
    except Exception as exc:
        raise HTTPException(400, f"抓取失败：{type(exc).__name__}: {exc}")
    name = (payload.get("title") or page.get("title") or "webpage")[:80]
    row, error = ingest.add_document(
        kb_id, name + ".md", (page.get("markdown") or "").encode("utf-8"),
        user["id"], tags=payload.get("tags") or [], channel="url",
        parse_mode="chunk", background=True)
    if error:
        raise HTTPException(400, error)
    services.audit(user["id"], "kb.doc.url", row["id"], url[:120])
    return row


@router.get("/{kb_id}/faq/entries")
def faq_entries(request: Request, kb_id: str):
    auth.current_user(request)
    _kb_or_404(kb_id)
    from . import faq
    return {"items": faq.list_entries(kb_id)}


@router.post("/{kb_id}/faq/entries")
def faq_create(request: Request, kb_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    _kb_or_404(kb_id)
    from . import faq
    try:
        row = faq.create_entry(kb_id, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    services.audit(user["id"], "kb.faq.create", kb_id,
                   payload.get("standard_question", "")[:60])
    return row


@router.put("/faq/entries/{entry_id}")
def faq_update(request: Request, entry_id: str,
               payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    from . import faq
    row = faq.update_entry(entry_id, payload)
    if not row:
        raise HTTPException(404, "FAQ 条目不存在")
    services.audit(user["id"], "kb.faq.update", entry_id, "")
    return row


@router.delete("/faq/entries/{entry_id}")
def faq_delete(request: Request, entry_id: str):
    user = auth.require(request, "editor")
    from . import faq
    if not faq.delete_entry(entry_id):
        raise HTTPException(404, "FAQ 条目不存在")
    services.audit(user["id"], "kb.faq.delete", entry_id, "")
    return {"ok": True}


@router.post("/{kb_id}/faq/import")
def faq_import(request: Request, kb_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    _kb_or_404(kb_id)
    from . import faq
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise HTTPException(400, "entries 必须是数组")
    try:
        result = faq.import_entries(
            kb_id, entries, payload.get("mode") or "append",
            bool(payload.get("dry_run")))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    services.audit(user["id"], "kb.faq.import", kb_id,
                   str(result.get("created", 0)))
    return result


@router.get("/{kb_id}/faq/export")
def faq_export(request: Request, kb_id: str, fmt: str = "json"):
    auth.current_user(request)
    _kb_or_404(kb_id)
    from . import faq
    content, kind = faq.export_entries(kb_id, fmt)
    return {"format": kind, "content": content}


@router.post("/{kb_id}/faq/search")
def faq_search(request: Request, kb_id: str, payload: dict = Body(default={})):
    auth.current_user(request)
    _kb_or_404(kb_id)
    from . import faq
    return {"items": faq.search(
        kb_id, payload.get("query") or "",
        payload.get("threshold") or 0.7,
        payload.get("match_count") or 10)}


@router.post("/documents/{doc_id}/sensitive/rescan")
def sensitive_rescan(request: Request, doc_id: str):
    user = auth.require(request, "editor")
    doc = ingest.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    from . import sensitive
    text = "\n".join(c["content"] for c in ingest.chunks(doc_id))
    result = sensitive.scan([("body", text)])
    ingest._save_sensitive(doc_id, result)
    services.audit(user["id"], "kb.sensitive.rescan", doc_id,
                   result.decision)
    return {"decision": result.decision, "level": result.level,
            "findings": len(result.findings)}


@router.get("/documents/{doc_id}/sensitive")
def sensitive_findings(request: Request, doc_id: str):
    auth.current_user(request)
    rows = db.q("SELECT * FROM sensitive_findings WHERE document_id=? "
                "ORDER BY created_at DESC", (doc_id,))
    for row in rows:
        row["findings"] = services.jload(row.get("findings"), [])
    return {"items": rows}


@router.post("/{kb_id}/dedup/scan")
def dedup_scan(request: Request, kb_id: str):
    user = auth.require(request, "editor")
    _kb_or_404(kb_id)
    from . import dedup
    result = dedup.scan_kb(kb_id)
    services.audit(user["id"], "kb.dedup.scan", kb_id,
                   str(result.get("relations", 0)))
    return result


@router.get("/{kb_id}/duplicates")
def duplicates(request: Request, kb_id: str):
    auth.current_user(request)
    _kb_or_404(kb_id)
    from . import dedup
    return {"items": dedup.list_relations(kb_id)}


@router.post("/duplicates/{relation_id}/resolve")
def resolve_duplicate(request: Request, relation_id: str,
                      payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    from . import dedup
    try:
        row = dedup.resolve(relation_id, payload.get("status") or "ignored")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    services.audit(user["id"], "kb.dedup.resolve", relation_id,
                   payload.get("status") or "ignored")
    return row


@router.get("/{kb_id}/recycle")
def list_recycle(request: Request, kb_id: str):
    auth.current_user(request)
    _kb_or_404(kb_id)
    return {"items": ingest.list_recycle(kb_id)}


@router.post("/recycle/{entry_id}/restore")
def restore_recycle(request: Request, entry_id: str):
    user = auth.require(request, "editor")
    row, error = ingest.restore_document(entry_id)
    if error:
        raise HTTPException(400, error)
    services.audit(user["id"], "kb.recycle.restore", entry_id, "")
    return row


@router.delete("/recycle/{entry_id}")
def purge_recycle(request: Request, entry_id: str):
    user = auth.require(request, "editor")
    if not ingest.purge_recycle(entry_id):
        raise HTTPException(404, "回收站条目不存在")
    services.audit(user["id"], "kb.recycle.purge", entry_id, "")
    return {"ok": True}


@router.post("/documents/batch")
def batch_documents(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    kb_id = (payload.get("kb_id") or "").strip()
    _kb_or_404(kb_id)
    action = (payload.get("action") or "").strip()
    if action not in ("reparse", "delete", "validity", "tags", "move"):
        raise HTTPException(400, "未知操作")
    payload["user_id"] = user["id"]
    result = ingest.batch_documents(kb_id, payload.get("ids") or [],
                                    action, payload)
    services.audit(user["id"], f"kb.batch.{action}", kb_id,
                   f"{result['ok']}")
    return result


@router.get("/chunks/{chunk_id}/revisions")
def chunk_revisions(request: Request, chunk_id: str):
    auth.current_user(request)
    return {"items": ingest.chunk_revisions(chunk_id)}


@router.patch("/chunks/{chunk_id}")
def update_chunk(request: Request, chunk_id: str,
                 payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row, error = ingest.update_chunk(
        chunk_id, content=payload.get("content"),
        enabled=payload.get("enabled"),
        expected_revision=payload.get("expected_revision"),
        editor_id=user["id"])
    if error:
        raise HTTPException(409 if "刷新" in error else 400, error)
    services.audit(user["id"], "kb.chunk.update", chunk_id, "")
    return row


@router.post("/chunks/{chunk_id}/revert/{revision}")
def revert_chunk(request: Request, chunk_id: str, revision: int):
    user = auth.require(request, "editor")
    row, error = ingest.revert_chunk(chunk_id, revision, user["id"])
    if error:
        raise HTTPException(409 if "刷新" in error else 400, error)
    services.audit(user["id"], "kb.chunk.revert", chunk_id, str(revision))
    return row


@router.get("/{kb_id}/tags")
def list_tags(request: Request, kb_id: str):
    auth.current_user(request)
    _kb_or_404(kb_id)
    rows = db.q("SELECT t.*, (SELECT COUNT(*) FROM document_tags d "
                "WHERE d.tag_id=t.id) documents FROM kb_tags t "
                "WHERE t.kb_id=? ORDER BY t.name", (kb_id,))
    return {"items": rows}


@router.post("/{kb_id}/tags")
def create_tag(request: Request, kb_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    _kb_or_404(kb_id)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    if db.one("SELECT id FROM kb_tags WHERE kb_id=? AND name=?",
              (kb_id, name)):
        raise HTTPException(400, "标签已存在")
    import hashlib
    tag_id = "kt_" + hashlib.md5(
        f"{kb_id}:{name}".encode("utf-8")).hexdigest()[:12]
    db.x("INSERT INTO kb_tags(id,kb_id,name,color,sort_order,created_at) "
         "VALUES(?,?,?,?,?,?)",
         (tag_id, kb_id, name, payload.get("color") or "",
          int(payload.get("sort_order") or 0), services.now()))
    services.audit(user["id"], "kb.tag.create", kb_id, name)
    return db.one("SELECT * FROM kb_tags WHERE id=?", (tag_id,))


@router.delete("/{kb_id}/tags/{tag_id}")
def delete_tag(request: Request, kb_id: str, tag_id: str,
               force: bool = False):
    user = auth.require(request, "editor")
    _kb_or_404(kb_id)
    count = db.one("SELECT COUNT(*) c FROM document_tags WHERE tag_id=?",
                   (tag_id,))["c"]
    if count and not force:
        raise HTTPException(400, "标签仍被文档引用，需 force=true")
    db.x("DELETE FROM document_tags WHERE tag_id=?", (tag_id,))
    db.x("DELETE FROM kb_tags WHERE id=? AND kb_id=?", (tag_id, kb_id))
    services.audit(user["id"], "kb.tag.delete", tag_id, str(count))
    return {"ok": True}


@router.get("/{kb_id}/folders")
def list_folders(request: Request, kb_id: str):
    auth.current_user(request)
    _kb_or_404(kb_id)
    return {"items": ingest.folders(kb_id)}


@router.post("/documents/{doc_id}/reparse")
def reparse_document(request: Request, doc_id: str,
                     payload: dict = Body(default=None)):
    user = auth.require(request, "editor")
    row = ingest.reparse_document(doc_id, payload or None)
    if not row:
        raise HTTPException(404, "文档不存在")
    services.audit(user["id"], "kb.doc.reparse", doc_id, "")
    return row


@router.get("/documents/{doc_id}/chunks")
def document_chunks(request: Request, doc_id: str):
    auth.current_user(request)
    if not ingest.get_document(doc_id):
        raise HTTPException(404, "文档不存在")
    return {"items": ingest.chunks(doc_id)}


@router.post("/documents/{doc_id}/enrich")
def enrich_document(request: Request, doc_id: str,
                    payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    if not ingest.get_document(doc_id):
        raise HTTPException(404, "文档不存在")
    from . import enrich
    result = enrich.enrich_document(doc_id, force=bool(payload.get("force")))
    services.audit(user["id"], "kb.doc.enrich", doc_id, ",".join(result))
    return result


@router.post("/documents/{doc_id}/summary")
def regenerate_summary(request: Request, doc_id: str):
    user = auth.require(request, "editor")
    if not ingest.get_document(doc_id):
        raise HTTPException(404, "文档不存在")
    from . import enrich
    summary, error = enrich.summarize(doc_id, force=True)
    if error:
        raise HTTPException(400, error)
    services.audit(user["id"], "kb.doc.summary", doc_id, "")
    return {"summary": summary}


@router.get("/documents/{doc_id}/versions")
def document_versions(request: Request, doc_id: str):
    auth.current_user(request)
    rows = db.q("SELECT id,version_no,source,chars,status,created_at,"
                "activated_at FROM document_versions WHERE document_id=? "
                "ORDER BY version_no DESC", (doc_id,))
    return {"items": rows}


@router.post("/documents/precheck")
def duplicate_precheck(request: Request, payload: dict = Body(default={})):
    """Browser-computed SHA-256 pre-check before uploading a batch."""
    auth.current_user(request)
    kb_id = (payload.get("kb_id") or "").strip()
    _kb_or_404(kb_id)
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise HTTPException(400, "items 必须是数组")
    out = []
    for item in items[:100]:
        sha = str(item.get("sha") or "").strip()
        name = item.get("name") or ""
        existing = db.one(
            "SELECT id,name FROM documents WHERE library_id=? AND "
            "file_hash=? AND deleted_at IS NULL", (kb_id, sha)) if sha else None
        out.append({"name": name, "sha": sha,
                    "exists": bool(existing),
                    "existing": {"id": existing["id"], "name": existing["name"]}
                    if existing else None})
    return {"items": out}


@router.post("/preview-chunking")
def preview_chunking(request: Request, payload: dict = Body(default={})):
    auth.current_user(request)
    text = payload.get("text") or ""
    if not text.strip():
        raise HTTPException(400, "示例文本不能为空")
    from ..chunker import SplitterConfig
    from ..chunker.strategy import preview
    cfg = (payload.get("config") or {}).get("chunking") or {}
    result = preview(text, SplitterConfig(
        chunk_size=int(cfg.get("chunk_size") or 512),
        chunk_overlap=int(cfg.get("chunk_overlap") or 80),
        strategy=cfg.get("strategy") or "auto",
        token_limit=int(cfg.get("token_limit") or 0),
        languages=cfg.get("languages") or [],
        separators=cfg.get("separators") or []))
    return result


@router.post("/{kb_id}/search")
def search(request: Request, kb_id: str, payload: dict = Body(default={})):
    auth.current_user(request)
    _kb_or_404(kb_id)
    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "检索内容不能为空")
    return {"items": retrieval.search(
        [kb_id], query, int(payload.get("top_k") or 8),
        use_vector=payload.get("use_vector", True),
        use_rerank=payload.get("use_rerank", True))}