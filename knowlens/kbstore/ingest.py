# -*- coding: utf-8 -*-
"""Unified document ingestion: parse → chunk → persist → index.

One pipeline for both parse modes:

* ``chunk`` — adaptive chunker (heading / heuristic / legacy, parent-child)
* ``tree``  — page chunks from PDF page markers (section tree is added by the
  tree builder when a model is configured)

Documents are stored under ``DATA/kbstore/<kb>/<doc>/`` and every parsed
markdown snapshot is kept in ``document_versions`` so re-parsing and rollback
share one code path.
"""
import hashlib
import json
import os
import threading
import time

from .. import config, db
from ..chunker import SplitterConfig, split, split_parent_child
from ..platform import services
from . import parsers, schema

ROOT = os.path.join(config.DATA, "kbstore")
LOCK = threading.Semaphore(2)
MAX_FILE_SIZE = 50 * 1024 * 1024


def init():
    schema.init()


def _kind(name):
    return parsers.kind_of(name)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _doc_dir(kb_id, doc_id):
    path = os.path.join(ROOT, kb_id, doc_id)
    os.makedirs(path, exist_ok=True)
    return path


def add_document(kb_id, name, data, user_id="", parse_mode="chunk",
                 parse_config=None, tags=None, validity_mode="inherit",
                 valid_from="", expires_at="", duplicate_policy="reject",
                 background=True, channel="web", folder_path=""):
    """Persist an upload and start processing. Returns (row, error)."""
    if not db.one("SELECT id FROM libraries WHERE id=?", (kb_id,)):
        return None, "知识库不存在"
    if len(data) > MAX_FILE_SIZE:
        return None, "文件超过 50MB"
    if parsers.is_video(name):
        return None, "暂不支持视频文件"
    if not parsers.is_supported(name):
        return None, f"不支持的文件类型：{_kind(name) or '未知'}"
    digest = _sha(data)
    same = db.one("SELECT id,name FROM documents WHERE library_id=? AND "
                  "file_hash=? AND deleted_at IS NULL", (kb_id, digest))
    if same and duplicate_policy != "allow":
        return None, f"内容相同，已存在：{same['name']}"
    doc_id = services.uid("dc_")
    folder = _doc_dir(kb_id, doc_id)
    safe_name = os.path.basename(name or "upload")
    path = os.path.join(folder, safe_name)
    with open(path, "wb") as fh:
        fh.write(data)
    now = services.now()
    record = {
        "id": doc_id, "space_id": "", "library_id": kb_id,
        "name": safe_name, "kind": _kind(safe_name), "doc_id": "",
        "status": "pending", "pages": 0, "description": "", "summary": "",
        "source_path": path, "error": "", "image_count": 0, "vision": 1,
        "ocr_status": "", "parse_mode": parse_mode or "chunk",
        "parse_config": json.dumps(parse_config or {}, ensure_ascii=False),
        "file_hash": digest, "file_size": len(data), "chars": 0,
        "chunks_count": 0, "content_revision": 0, "active_version_id": "",
        "pending_version_id": "", "version_no": 0,
        "validity_mode": validity_mode or "inherit",
        "valid_from": valid_from or "", "expires_at": expires_at or "",
        "enabled": 1,
        "folder_path": "/".join([
            part for part in (folder_path or "").replace("\\", "/").split("/")
            if part and part not in (".", "..")][:16]),
        "channel": channel or "web",
        "tags": json.dumps(tags or [], ensure_ascii=False),
        "created_at": now, "updated_at": now,
    }
    _insert("documents", record)
    _attach_tags(kb_id, doc_id, tags)
    if background:
        threading.Thread(target=process_document, args=(doc_id,),
                         daemon=True).start()
    return db.one("SELECT * FROM documents WHERE id=?", (doc_id,)), ""


def _insert(table, values):
    cols = list(values)
    sql = (f"INSERT OR IGNORE INTO {table}({','.join(cols)}) "
           f"VALUES({','.join('?' * len(cols))})")
    db.x(sql, tuple(values[c] for c in cols))


def _attach_tags(kb_id, doc_id, tags):
    for name in tags or []:
        name = str(name).strip()
        if not name:
            continue
        row = db.one("SELECT id FROM kb_tags WHERE kb_id=? AND name=?",
                     (kb_id, name))
        if row:
            tag_id = row["id"]
        else:
            tag_id = "kt_" + hashlib.md5(
                f"{kb_id}:{name}".encode("utf-8")).hexdigest()[:12]
            _insert("kb_tags", {"id": tag_id, "kb_id": kb_id, "name": name,
                                "created_at": services.now()})
        _insert("document_tags", {"document_id": doc_id, "tag_id": tag_id})


# ────────────────────────── processing ──────────────────────────

def process_document(doc_id):
    with LOCK:
        doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        if not doc:
            return
        db.x("UPDATE documents SET status='processing',error='',updated_at=? "
             "WHERE id=?", (services.now(), doc_id))
        try:
            _process(doc)
        except Exception as exc:
            db.x("UPDATE documents SET status='failed',error=?,updated_at=? "
                 "WHERE id=?",
                 (f"{type(exc).__name__}: {exc}"[:500], services.now(), doc_id))


def _config_for(doc):
    kb = db.one("SELECT * FROM libraries WHERE id=?", (doc["library_id"],))
    kb_config = services.jload(kb.get("config") if kb else "{}", {})
    doc_config = services.jload(doc.get("parse_config"), {})
    chunking = dict(kb_config.get("chunking") or {})
    chunking.update(doc_config.get("chunking") or {})
    return kb, kb_config, chunking, doc_config


def _process(doc):
    kb, kb_config, chunking, doc_config = _config_for(doc)
    data = b""
    with open(doc["source_path"], "rb") as fh:
        data = fh.read()
    engine = (doc_config.get("parser_engine")
              or parsers.resolve_engine(doc["kind"],
                                        chunking.get("parser_engines")))
    parsed = parsers.parse(data, doc["name"], engine=engine,
                           overrides=doc_config.get("parser_overrides") or {})
    if parsed.error:
        raise RuntimeError(parsed.error)
    markdown = parsed.markdown or ""

    from . import sensitive
    sens_cfg = (kb_config.get("sensitive") or {})
    decision, markdown, sens_result = sensitive.apply_gate(
        doc["name"], markdown, sens_cfg)
    if sens_result is not None:
        _save_sensitive(doc["id"], sens_result)
    if sens_cfg.get("enabled") and decision in ("block", "review"):
        db.x("UPDATE documents SET status='failed',enabled=0,error=?,"
             "updated_at=? WHERE id=?",
             (f"sensitive content {decision} "
              f"({len(sens_result.findings)} findings); content was not indexed",
              services.now(), doc["id"]))
        return
    version_id = _save_version(doc, markdown, parsed.metadata)
    _clear_chunks(doc["id"])

    if (doc.get("parse_mode") or "chunk") == "tree":
        chunks = _page_chunks(markdown, parsed.metadata, chunking)
        chunks += _section_chunks(markdown, parsed.metadata,
                                  start_index=len(chunks))
    else:
        chunks = _split_chunks(markdown, chunking,
                                parsed.metadata.get("page_map") or [])
    written = _write_chunks(doc, chunks, markdown)
    try:
        from . import retrieval
        retrieval.index_embeddings(doc["library_id"], doc["id"], written)
    except Exception:
        pass
    total_chars = len(markdown)
    db.x("UPDATE documents SET status='ready',chars=?,pages=?,chunks_count=?,"
         "version_no=CASE WHEN version_no>0 THEN version_no ELSE 1 END,"
         "active_version_id=?,enabled=1,error='',updated_at=? WHERE id=?",
         (total_chars, int(parsed.metadata.get("pages") or 0),
          len(chunks), version_id, services.now(), doc["id"]))
    rebuild_fts(kb_id=doc["library_id"])
    try:
        from . import enrich
        kb_cfg = services.jload(kb.get("config"), {}) or {}
        if kb_cfg.get("enrichment"):
            threading.Thread(target=enrich.enrich_document,
                             args=(doc["id"],), daemon=True).start()
        if (kb_cfg.get("dedup") or {}).get("enabled"):
            from . import dedup
            threading.Thread(target=dedup.scan_document,
                             args=(doc["id"],), daemon=True).start()
    except Exception:
        pass


def _save_sensitive(doc_id, result):
    _insert("sensitive_findings", {
        "id": services.uid("sf_"), "document_id": doc_id,
        "decision": result.decision, "level": result.level,
        "findings": json.dumps([
            {"category": f.category, "rule": f.rule_id, "action": f.action,
             "sample": f.sample} for f in result.findings], ensure_ascii=False),
        "signature": result.signature, "status": "pending",
        "created_at": services.now()})


def _save_version(doc, markdown, metadata):
    version_id = services.uid("dv_")
    folder = os.path.dirname(doc["source_path"])
    path = os.path.join(folder, f"{version_id}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    version_no = int(doc.get("version_no") or 0) + 1
    _insert("document_versions", {
        "id": version_id, "document_id": doc["id"],
        "version_no": version_no, "source": "parse", "md_path": path,
        "md_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "chars": len(markdown), "status": "active",
        "created_at": services.now(), "activated_at": services.now()})
    db.x("UPDATE documents SET version_no=? WHERE id=?",
         (version_no, doc["id"]))
    return version_id


def _split_chunks(markdown, chunking, page_map=None):
    page_map = page_map or []
    cfg = SplitterConfig(
        chunk_size=int(chunking.get("chunk_size") or 512),
        chunk_overlap=int(chunking.get("chunk_overlap") or 80),
        separators=chunking.get("separators") or [],
        strategy=chunking.get("strategy") or "auto",
        token_limit=int(chunking.get("token_limit") or 0),
        languages=chunking.get("languages") or [])
    parent_child = chunking.get("parent_child") or {}
    if parent_child.get("enabled"):
        result = split_parent_child(
            markdown, cfg,
            parent_size=int(parent_child.get("parent_size") or 4096),
            child_size=int(parent_child.get("child_size") or 384))
        out = []
        for index, parent in enumerate(result.parents):
            out.append({"type": "parent_text", "content": parent.content,
                        "start": parent.start, "end": parent.end,
                        "header": parent.context_header, "parent": -1,
                        "idx": index,
                        "page": _page_at(page_map, parent.start)})
        offset = len(result.parents)
        for index, (child, parent_index) in enumerate(result.children):
            out.append({"type": "text", "content": child.content,
                        "start": child.start, "end": child.end,
                        "header": child.context_header,
                        "parent": parent_index, "idx": offset + index,
                        "page": _page_at(page_map, child.start)})
        return out
    return [{"type": "text", "content": chunk.content,
             "start": chunk.start, "end": chunk.end,
             "header": chunk.context_header, "parent": -1, "idx": index,
             "page": _page_at(page_map, chunk.start)}
            for index, chunk in enumerate(split(markdown, cfg))]


def _page_chunks(markdown, metadata, chunking):
    chunks, page_map = [], metadata.get("page_map") or []
    for index, chunk in enumerate(_iter_pages(markdown)):
        page = _page_at(page_map, chunk["start"])
        chunks.append({"type": "page", "content": chunk["content"],
                       "start": chunk["start"], "end": chunk["end"],
                       "header": "", "parent": -1, "idx": index,
                       "page": page})
    return chunks


def _iter_pages(markdown):
    """Split markdown on ``<!-- page:n -->`` markers."""
    import re
    parts = re.split(r"<!--\s*page:(\d+)\s*-->", markdown)
    # parts: [pre, number, text, number, text, ...]
    pos = len(parts[0]) if parts else 0
    out = []
    if parts and parts[0].strip():
        out.append({"content": parts[0], "start": 0, "end": len(parts[0]),
                    "page": 1})
    for index in range(1, len(parts), 2):
        number = int(parts[index]) if parts[index].isdigit() else 1
        text = parts[index + 1] if index + 1 < len(parts) else ""
        marker_len = len(f"<!-- page:{parts[index]} -->")
        start = pos + marker_len
        body = text
        if body.strip():
            out.append({"content": body, "start": start,
                        "end": start + len(body), "page": number})
        pos = start + len(body)
    return out


def _section_chunks(markdown, metadata, start_index=0):
    """ATX-heading sections with nesting and page ranges (tree mode)."""
    page_map = metadata.get("page_map") or []
    lines = markdown.split("\n")
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    headings = []
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = __import__("re").match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append({"level": len(match.group(1)),
                             "title": match.group(2).strip(),
                             "line": index, "start": offsets[index]})
    if not headings:
        return []
    for i, heading in enumerate(headings):
        end_line = len(lines)
        for other in headings[i + 1:]:
            if other["level"] <= heading["level"]:
                end_line = other["line"]
                break
        heading["end"] = offsets[end_line] if end_line < len(offsets) else len(markdown)
    stack, out = [], []
    for heading in headings:
        while stack and stack[-1]["level"] >= heading["level"]:
            stack.pop()
        heading["parent"] = stack[-1] if stack else None
        stack.append(heading)
        body = markdown[heading["start"]:heading["end"]].strip()
        if not body:
            continue
        breadcrumb = ""
        chain = [h["title"] for h in stack]
        if chain:
            breadcrumb = "\n".join(
                "#" * h["level"] + " " + h["title"] for h in stack)
        out.append({
            "type": "section",
            "content": body[:4000],
            "start": heading["start"], "end": heading["end"],
            "header": breadcrumb, "heading": heading["title"],
            "parent": heading["parent"]["idx"] if heading.get("parent") else -1,
            "idx": start_index + len(out),
            "page": _page_at(page_map, heading["start"]),
            "page_end": _page_at(page_map, max(heading["start"], heading["end"] - 1)),
        })
        heading["idx"] = out[-1]["idx"]
    return out


def _page_at(page_map, offset):
    for entry in page_map:
        if offset <= entry.get("end", 0):
            return int(entry.get("page") or 0)
    return int(page_map[-1]["page"]) if page_map else 1


def _clear_chunks(doc_id):
    rows = db.q("SELECT rid FROM chunks WHERE document_id=?", (doc_id,))
    for row in rows:
        db.x("DELETE FROM chunks_fts WHERE rowid=?", (row["rid"],))
    db.x("DELETE FROM chunks WHERE document_id=?", (doc_id,))


def _write_chunks(doc, chunks, markdown):
    parent_ids, written = {}, []
    for chunk in chunks:
        cid = f"ch_{doc['id']}_{chunk['idx']}"
        page = chunk.get("page")
        if page is None and chunk["type"] == "page":
            page = 0
        rid = db.x(
            "INSERT INTO chunks(id,kb_id,document_id,chunk_index,chunk_type,"
            "content,source_content,context_header,heading,page,page_end,"
            "start_at,end_at,parent_chunk_id,enabled,flags,index_status,"
            "metadata,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, doc["library_id"], doc["id"], chunk["idx"], chunk["type"],
             chunk["content"], "", chunk.get("header") or "",
             chunk.get("heading") or "",
             int(page or 0), int(page or 0), chunk["start"], chunk["end"],
             parent_ids.get(chunk.get("parent", -1), ""), 1, 1, "ready",
             json.dumps({"page": page or 0}, ensure_ascii=False),
             services.now(), services.now()))
        parent_ids[chunk["idx"]] = cid
        db.x("INSERT INTO chunks_fts(rowid,content,heading,context_header) "
             "VALUES(?,?,?,?)",
             (rid, chunk["content"], chunk.get("heading") or "",
              chunk.get("header") or ""))
        written.append({"id": cid, "content": chunk["content"]})
    return written


# ────────────────────────── maintenance ──────────────────────────

def reparse_document(doc_id, parse_config=None):
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not doc:
        return None
    if parse_config is not None:
        db.x("UPDATE documents SET parse_config=?,updated_at=? WHERE id=?",
             (json.dumps(parse_config, ensure_ascii=False),
              services.now(), doc_id))
    else:
        db.x("UPDATE documents SET parse_config='{}',updated_at=? WHERE id=?",
             (services.now(), doc_id))
    db.x("UPDATE documents SET status='pending',error='',updated_at=? "
         "WHERE id=?", (services.now(), doc_id))
    threading.Thread(target=process_document, args=(doc_id,),
                     daemon=True).start()
    return db.one("SELECT * FROM documents WHERE id=?", (doc_id,))


def delete_document(doc_id, purge=False):
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not doc:
        return False
    _clear_chunks(doc_id)
    db.x("DELETE FROM chunk_embeddings WHERE document_id=?", (doc_id,))
    versions = db.q("SELECT md_path FROM document_versions WHERE document_id=?",
                    (doc_id,))
    db.x("DELETE FROM document_versions WHERE document_id=?", (doc_id,))
    db.x("DELETE FROM document_tags WHERE document_id=?", (doc_id,))
    for row in versions:
        try:
            os.remove(row["md_path"])
        except OSError:
            pass
    db.x("DELETE FROM documents WHERE id=?", (doc_id,))
    if purge:
        import shutil
        shutil.rmtree(os.path.join(ROOT, doc["library_id"], doc_id),
                      ignore_errors=True)
    rebuild_fts(kb_id=doc["library_id"])
    return True


def rebuild_fts(kb_id=""):
    conn = db.conn()
    if kb_id:
        conn.execute("DELETE FROM chunks_fts WHERE rowid IN "
                     "(SELECT rid FROM chunks WHERE kb_id=?)", (kb_id,))
        conn.execute(
            "INSERT INTO chunks_fts(rowid,content,heading,context_header) "
            "SELECT rid,content,heading,context_header FROM chunks "
            "WHERE kb_id=? AND enabled=1", (kb_id,))
    else:
        conn.execute("DELETE FROM chunks_fts")
        conn.execute(
            "INSERT INTO chunks_fts(rowid,content,heading,context_header) "
            "SELECT rid,content,heading,context_header FROM chunks "
            "WHERE enabled=1")
    conn.commit()


# ────────────────────────── governance ──────────────────────────

RECYCLE_DAYS = 90


def soft_delete_document(doc_id, user_id=""):
    doc = db.one("SELECT * FROM documents WHERE id=? AND deleted_at IS NULL",
                 (doc_id,))
    if not doc:
        return False
    now = services.now()
    expires = time.strftime("%Y-%m-%d %H:%M:%S",
                            time.localtime(time.time() + RECYCLE_DAYS * 86400))
    db.x("UPDATE documents SET deleted_at=?,updated_at=? WHERE id=?",
         (now, now, doc_id))
    _insert("recycle_entries", {
        "id": services.uid("rc_"), "kb_id": doc["library_id"],
        "kind": "document", "ref_id": doc_id, "name": doc["name"],
        "deleted_by": user_id or "", "expires_at": expires,
        "created_at": now})
    rebuild_fts(kb_id=doc["library_id"])
    return True


def list_recycle(kb_id):
    rows = db.q("SELECT * FROM recycle_entries WHERE kb_id=? AND "
                "expires_at>? ORDER BY created_at DESC",
                (kb_id, services.now()))
    return rows


def restore_document(entry_id):
    entry = db.one("SELECT * FROM recycle_entries WHERE id=?", (entry_id,))
    if not entry:
        return None, "回收站条目不存在"
    if entry["expires_at"] <= services.now():
        return None, "已超过保留期"
    doc = db.one("SELECT * FROM documents WHERE id=?", (entry["ref_id"],))
    if not doc:
        return None, "文档已被彻底删除"
    db.x("UPDATE documents SET deleted_at=NULL,updated_at=? WHERE id=?",
         (services.now(), entry["ref_id"]))
    db.x("DELETE FROM recycle_entries WHERE id=?", (entry_id,))
    rebuild_fts(kb_id=entry["kb_id"])
    return get_document(entry["ref_id"]), ""


def purge_recycle(entry_id):
    entry = db.one("SELECT * FROM recycle_entries WHERE id=?", (entry_id,))
    if not entry:
        return False
    delete_document(entry["ref_id"], purge=True)
    db.x("DELETE FROM recycle_entries WHERE id=?", (entry_id,))
    return True


def purge_expired():
    rows = db.q("SELECT id FROM recycle_entries WHERE expires_at<=?",
                (services.now(),))
    for row in rows:
        purge_recycle(row["id"])
    return len(rows)


def update_chunk(chunk_id, content=None, enabled=None, expected_revision=None,
                 editor_id=""):
    """Edit a text chunk with optimistic locking; keep a revision snapshot."""
    row = db.one("SELECT * FROM chunks WHERE id=?", (chunk_id,))
    if not row:
        return None, "分块不存在"
    if row["chunk_type"] not in ("text", "page"):
        return None, "该类型分块不可编辑"
    if (expected_revision is not None
            and int(row.get("content_revision") or 0) != int(expected_revision)):
        return None, "分块已被修改，请刷新后重试"
    new_content = row["content"] if content is None else str(content)
    new_enabled = (1 if row.get("enabled", 1) else 0) if enabled is None         else (1 if enabled else 0)
    if content is not None and not new_content.strip():
        return None, "分块内容不能为空"
    revision = int(row.get("content_revision") or 0) + 1
    _insert("chunk_revisions", {
        "id": services.uid("cr_"), "chunk_id": chunk_id,
        "revision": row.get("content_revision") or 0,
        "content": row["content"], "enabled": row.get("enabled", 1),
        "editor_id": editor_id or "", "edited_at": services.now(),
        "created_at": services.now()})
    source = row.get("source_content") or row["content"]
    db.x("UPDATE chunks SET content=?,source_content=?,enabled=?,"
         "content_revision=?,last_editor_id=?,index_status='ready',"
         "updated_at=? WHERE id=?",
         (new_content, source, new_enabled, revision, editor_id or "",
          services.now(), chunk_id))
    db.x("DELETE FROM chunks_fts WHERE rowid=?", (row["rid"],))
    if new_enabled:
        db.x("INSERT INTO chunks_fts(rowid,content,heading,context_header) "
             "VALUES(?,?,?,?)",
             (row["rid"], new_content, row.get("heading") or "",
              row.get("context_header") or ""))
    db.x("UPDATE documents SET content_revision=content_revision+1,"
         "updated_at=? WHERE id=?", (services.now(), row["document_id"]))
    return db.one("SELECT * FROM chunks WHERE id=?", (chunk_id,)), ""


def chunk_revisions(chunk_id):
    return db.q("SELECT id,revision,content,enabled,editor_id,edited_at "
                "FROM chunk_revisions WHERE chunk_id=? ORDER BY revision DESC",
                (chunk_id,))


def revert_chunk(chunk_id, revision, editor_id=""):
    row = db.one("SELECT * FROM chunk_revisions WHERE chunk_id=? AND "
                 "revision=?", (chunk_id, int(revision)))
    if not row:
        return None, "该修订不存在"
    current = db.one("SELECT content_revision FROM chunks WHERE id=?",
                     (chunk_id,))
    return update_chunk(chunk_id, content=row["content"],
                        enabled=bool(row["enabled"]),
                        expected_revision=current["content_revision"],
                        editor_id=editor_id)


def batch_documents(kb_id, ids, action, payload=None):
    payload = payload or {}
    ids = [i for i in (ids or []) if i]
    out = {"ok": 0, "errors": []}
    for doc_id in ids:
        doc = db.one("SELECT * FROM documents WHERE id=? AND library_id=?",
                     (doc_id, kb_id))
        if not doc:
            out["errors"].append({"id": doc_id, "error": "文档不存在"})
            continue
        try:
            if action == "reparse":
                reparse_document(doc_id, payload.get("parse_config"))
            elif action == "delete":
                soft_delete_document(doc_id, payload.get("user_id", ""))
            elif action == "validity":
                update_document(doc_id,
                                valid_from=payload.get("valid_from"),
                                expires_at=payload.get("expires_at"),
                                validity_mode=payload.get("validity_mode"))
            elif action == "tags":
                update_document(doc_id, tags=payload.get("tags") or [])
            elif action == "move":
                db.x("UPDATE documents SET folder_path=?,updated_at=? "
                     "WHERE id=?",
                     ((payload.get("folder_path") or "/").strip("/"),
                      services.now(), doc_id))
            else:
                out["errors"].append({"id": doc_id, "error": "未知操作"})
                continue
            out["ok"] += 1
        except Exception as exc:
            out["errors"].append({"id": doc_id,
                                  "error": f"{type(exc).__name__}: {exc}"})
    return out


def folders(kb_id):
    rows = db.q("SELECT folder_path, COUNT(*) c FROM documents WHERE "
                "library_id=? AND deleted_at IS NULL GROUP BY folder_path",
                (kb_id,))
    return [{"path": r["folder_path"] or "/", "count": r["c"]} for r in rows]


# ────────────────────────── queries ──────────────────────────

def list_documents(kb_id, status=""):
    sql = "SELECT * FROM documents WHERE library_id=? AND deleted_at IS NULL"
    args = [kb_id]
    if status:
        sql += " AND status=?"
        args.append(status)
    rows = db.q(sql + " ORDER BY created_at DESC", tuple(args))
    for row in rows:
        row["tags"] = _tags_for(row["id"])
        row["enabled"] = bool(row.get("enabled", 1))
    return rows


def _tags_for(doc_id):
    return [r["name"] for r in db.q(
        "SELECT t.name FROM document_tags d JOIN kb_tags t ON t.id=d.tag_id "
        "WHERE d.document_id=? ORDER BY t.name", (doc_id,))]


def update_document(doc_id, *, tags=None, enabled=None, valid_from=None,
                    expires_at=None, validity_mode=None):
    row = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not row:
        return None
    sets, args = [], []
    if valid_from is not None:
        sets.append("valid_from=?")
        args.append(str(valid_from)[:10])
    if expires_at is not None:
        sets.append("expires_at=?")
        args.append(str(expires_at)[:10])
    if validity_mode is not None:
        sets.append("validity_mode=?")
        args.append(str(validity_mode))
    if enabled is not None:
        sets.append("enabled=?")
        args.append(1 if enabled else 0)
        db.x("UPDATE chunks SET enabled=? WHERE document_id=?",
             (1 if enabled else 0, doc_id))
    if tags is not None:
        db.x("DELETE FROM document_tags WHERE document_id=?", (doc_id,))
        _attach_tags(row["library_id"], doc_id, tags)
    if sets:
        args.extend([services.now(), doc_id])
        db.x(f"UPDATE documents SET {','.join(sets)},updated_at=? WHERE id=?",
             args)
    return get_document(doc_id)


def get_document(doc_id):
    row = db.one("SELECT * FROM documents WHERE id=? AND deleted_at IS NULL",
                 (doc_id,))
    if row:
        row["tags"] = _tags_for(doc_id)
        row["enabled"] = bool(row.get("enabled", 1))
    return row


def chunks(doc_id):
    return db.q("SELECT id,chunk_index,chunk_type,content,context_header,"
                "heading,page,start_at,end_at,parent_chunk_id,enabled,"
                "index_status FROM chunks WHERE document_id=? "
                "ORDER BY chunk_index", (doc_id,))


def stats(kb_id):
    base = db.one("SELECT * FROM libraries WHERE id=?", (kb_id,))
    if not base:
        return None
    docs = db.one("SELECT COUNT(*) c FROM documents WHERE library_id=? AND "
                  "deleted_at IS NULL", (kb_id,))["c"]
    ready = db.one("SELECT COUNT(*) c FROM documents WHERE library_id=? AND "
                   "status='ready' AND deleted_at IS NULL", (kb_id,))["c"]
    processing = db.one("SELECT COUNT(*) c FROM documents WHERE library_id=? "
                        "AND status IN ('pending','processing') AND "
                        "deleted_at IS NULL", (kb_id,))["c"]
    chunk_count = db.one("SELECT COUNT(*) c FROM chunks WHERE kb_id=? AND "
                         "enabled=1", (kb_id,))["c"]
    return {"base": base, "documents": docs, "ready": ready,
            "processing": processing, "chunks": chunk_count}