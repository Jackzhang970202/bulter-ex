# -*- coding: utf-8 -*-
"""Materialise existing knowledge data into the unified store.

Two sources are migrated, both without re-parsing and without calling a model:

* ``kb2_*`` tables  → libraries / documents / chunks
* PageIndex dirs    → chunks(type=page) + chunks(type=section) for classic
  documents that already carry a ``doc_id``.

The migration is idempotent: ids are stable and inserts use ``OR IGNORE``.
The FTS index is rebuilt from ``chunks`` at the end of each run.
"""
import hashlib
import json
import os

from .. import config, db
from . import schema

CLI_HELP = "usage: python -m knowlens.kbstore.migrate [--dry-run]"
UNASSIGNED_SPACE = "sp_unassigned"

STATUS_MAP = {"ready": "ready", "failed": "failed", "pending": "pending",
              "processing": "processing", "completed": "ready"}


def _ensure_space():
    """Knowledge bases may exist outside a workspace; give them a home."""
    row = db.one("SELECT id FROM spaces WHERE id=?", (UNASSIGNED_SPACE,))
    if not row:
        _insert("spaces", {"id": UNASSIGNED_SPACE, "name": "未分组",
                           "description": "不属于任何空间的知识库",
                           "created_at": db.now()})
    return UNASSIGNED_SPACE


def _insert(table, values):
    cols = list(values)
    sql = (f"INSERT OR IGNORE INTO {table}({','.join(cols)}) "
           f"VALUES({','.join('?' * len(cols))})")
    db.x(sql, tuple(values[c] for c in cols))


def _exists_row(table, id_):
    return bool(db.one(f"SELECT 1 FROM {table} WHERE id=?", (id_,)))


def _json_load(value, default):
    if isinstance(value, type(default)):
        return value
    try:
        data = json.loads(value or "")
    except Exception:
        return default
    return data if isinstance(data, type(default)) else default


def _sha256(path):
    if not path or not os.path.exists(path):
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tables():
    return {row["name"] for row in
            db.q("SELECT name FROM sqlite_master WHERE type='table'")}


# ────────────────────────── kb2 ──────────────────────────

def migrate_kb2():
    tables = _tables()
    stats = {"bases": 0, "documents": 0, "chunks": 0, "tags": 0}
    if "kb2_bases" not in tables:
        return stats

    space_id = _ensure_space()
    for base in db.q("SELECT * FROM kb2_bases"):
        if not _exists_row("libraries", base["id"]):
            _insert("libraries", {
                "id": base["id"], "space_id": space_id, "name": base["name"],
                "description": base.get("description") or "",
                "created_at": base.get("created_at") or db.now()})
            stats["bases"] += 1
        config_json = {
            "chunking": {
                "strategy": "legacy",
                "chunk_size": int(base.get("chunk_size") or 512),
                "chunk_overlap": int(base.get("chunk_overlap") or 80),
            },
            "indexing": {"keyword": True, "vector": False, "tree": False},
        }
        db.x("UPDATE libraries SET type='document',config=? WHERE id=?",
             (json.dumps(config_json, ensure_ascii=False), base["id"]))

    if "kb2_knowledges" in tables:
        for doc in db.q("SELECT * FROM kb2_knowledges"):
            if not _exists_row("documents", doc["id"]):
                _insert("documents", {
                    "id": doc["id"], "space_id": space_id,
                    "library_id": doc["kb_id"],
                    "name": doc["title"], "kind": _kind_of(doc["title"]),
                    "doc_id": "", "status": STATUS_MAP.get(
                        doc.get("status") or "", "pending"),
                    "pages": int(doc.get("pages") or 0),
                    "description": "", "summary": "",
                    "source_path": doc.get("storage_path") or "",
                    "error": doc.get("error") or "", "image_count": 0,
                    "vision": 1, "ocr_status": "",
                    "parse_mode": "chunk", "parse_config": "{}",
                    "file_hash": doc.get("sha") or "", "file_size": 0,
                    "chars": int(doc.get("chars") or 0),
                    "chunks_count": int(doc.get("chunks_count") or 0),
                    "content_revision": 0, "active_version_id": "",
                    "pending_version_id": "",
                    "version_no": int(doc.get("version") or 1),
                    "validity_mode": "custom" if doc.get("valid_to") else "permanent",
                    "valid_from": doc.get("valid_from") or "",
                    "expires_at": doc.get("valid_to") or "",
                    "enabled": 1 if doc.get("enabled", 1) else 0,
                    "folder_path": "", "channel": "web",
                    "tags": doc.get("tags") or "[]",
                    "created_at": doc.get("created_at") or db.now(),
                    "updated_at": doc.get("updated_at") or db.now(),
                })
                stats["documents"] += 1
            stats["tags"] += _migrate_kb2_tags(doc)

    if "kb2_chunks" in tables:
        for chunk in db.q("SELECT * FROM kb2_chunks"):
            if db.one("SELECT 1 FROM chunks WHERE id=?", (chunk["id"],)):
                continue
            _insert("chunks", {
                "id": chunk["id"], "kb_id": chunk["kb_id"],
                "document_id": chunk["knowledge_id"],
                "chunk_index": int(chunk.get("idx") or 0),
                "chunk_type": "text", "content": chunk["content"],
                "source_content": "", "context_header": "",
                "heading": chunk.get("heading") or "",
                "page": int(chunk.get("page") or 0), "page_end": 0,
                "start_at": int(chunk.get("start_at") or 0),
                "end_at": int(chunk.get("end_at") or 0),
                "parent_chunk_id": "", "pre_chunk_id": "", "next_chunk_id": "",
                "enabled": 1 if chunk.get("enabled", 1) else 0,
                "flags": 1, "index_status": "ready",
                "content_revision": 0, "content_hash": "",
                "image_info": "{}", "metadata": "{}",
                "created_at": chunk.get("created_at") or db.now(),
                "updated_at": db.now(),
            })
            stats["chunks"] += 1
    return stats


def _migrate_kb2_tags(doc):
    tags = _json_load(doc.get("tags"), [])
    added = 0
    for name in tags:
        name = str(name).strip()
        if not name:
            continue
        row = db.one("SELECT id FROM kb_tags WHERE kb_id=? AND name=?",
                     (doc["kb_id"], name))
        if row:
            tag_id = row["id"]
        else:
            tag_id = "kt_" + hashlib.md5(
                f"{doc['kb_id']}:{name}".encode("utf-8")).hexdigest()[:12]
            _insert("kb_tags", {"id": tag_id, "kb_id": doc["kb_id"],
                                "name": name, "created_at": db.now()})
            added += 1
        _insert("document_tags", {"document_id": doc["id"], "tag_id": tag_id})
    return added


def _kind_of(name):
    return ((name or "").rsplit(".", 1)[-1].lower()) or "file"


# ────────────────────────── PageIndex ──────────────────────────

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def migrate_pageindex():
    """Materialise PageIndex pages/tree into chunks for classic documents."""
    stats = {"documents": 0, "pages": 0, "sections": 0}
    rows = db.q("SELECT * FROM documents WHERE doc_id IS NOT NULL "
                "AND doc_id<>''")
    for doc in rows:
        if db.one("SELECT 1 FROM chunks WHERE document_id=? LIMIT 1",
                  (doc["id"],)):
            continue
        folder = os.path.join(config.SPACES, doc["space_id"], "pi", "docs",
                              doc["doc_id"])
        pages = _read_json(os.path.join(folder, "pages.json")) or []
        tree = _read_json(os.path.join(folder, "tree.json")) or []
        if not pages:
            continue

        offset = 0
        page_offsets = {}
        page_count = 0
        for index, page in enumerate(pages):
            text = page.get("markdown") or ""
            number = int(page.get("page_index") or index + 1)
            page_offsets[number] = (offset, offset + len(text))
            _insert("chunks", {
                "id": f"chp_{doc['doc_id']}_{number}",
                "kb_id": doc["library_id"], "document_id": doc["id"],
                "chunk_index": index, "chunk_type": "page", "content": text,
                "source_content": "", "context_header": "", "heading": "",
                "page": number, "page_end": number, "start_at": offset,
                "end_at": offset + len(text), "enabled": 1, "flags": 1,
                "index_status": "ready", "metadata": "{}",
                "created_at": db.now(), "updated_at": db.now(),
            })
            offset += len(text)
            page_count += 1

        counter = {"n": 0, "sections": 0}

        def walk(nodes, parent_id):
            for node in nodes if isinstance(nodes, list) else []:
                if not isinstance(node, dict):
                    continue
                counter["n"] += 1
                node_id = str(node.get("node_id") or f"n{counter['n']:04d}")
                title = node.get("title") or ""
                start = int(node.get("start_index") or 1)
                end = int(node.get("end_index") or start)
                start_at, end_at = _page_span(page_offsets, start, end)
                cid = f"chs_{doc['doc_id']}_{node_id}"
                _insert("chunks", {
                    "id": cid, "kb_id": doc["library_id"],
                    "document_id": doc["id"],
                    "chunk_index": page_count + counter["n"],
                    "chunk_type": "section",
                    "content": node.get("summary") or title,
                    "source_content": "", "context_header": "",
                    "heading": title, "page": start, "page_end": end,
                    "start_at": start_at, "end_at": end_at,
                    "parent_chunk_id": parent_id, "enabled": 1, "flags": 1,
                    "index_status": "ready",
                    "metadata": json.dumps({"node_id": node_id},
                                           ensure_ascii=False),
                    "created_at": db.now(), "updated_at": db.now(),
                })
                counter["sections"] += 1
                walk(node.get("nodes") or [], cid)

        walk(tree, "")

        db.x("UPDATE documents SET parse_mode='tree',pages=?,chars=?,"
             "chunks_count=?,version_no=CASE WHEN version_no>0 THEN version_no "
             "ELSE 1 END,file_hash=? WHERE id=?",
             (page_count, offset, page_count + counter["sections"],
              _sha256(doc.get("source_path")), doc["id"]))
        stats["documents"] += 1
        stats["pages"] += page_count
        stats["sections"] += counter["sections"]
    return stats


def _page_span(page_offsets, start, end):
    if not page_offsets:
        return 0, 0
    first = page_offsets.get(start) or (0, 0)
    last = page_offsets.get(end) or first
    return first[0], last[1]


# ────────────────────────── FTS ──────────────────────────

def rebuild_fts():
    conn = db.conn()
    conn.execute("DELETE FROM chunks_fts")
    conn.execute(
        "INSERT INTO chunks_fts(rowid,content,heading,context_header) "
        "SELECT rid,content,heading,context_header FROM chunks WHERE enabled=1")
    conn.commit()
    return db.one("SELECT COUNT(*) c FROM chunks_fts")["c"]


def preview():
    """Count what is still pending, without writing anything."""
    tables = _tables()
    out = {"kb2": {"bases": 0, "documents": 0, "chunks": 0},
           "pageindex": {"documents": 0, "pages": 0}}
    if "kb2_bases" in tables:
        out["kb2"]["bases"] = db.one(
            "SELECT COUNT(*) c FROM kb2_bases b WHERE NOT EXISTS("
            "SELECT 1 FROM libraries l WHERE l.id=b.id)")["c"]
    if "kb2_knowledges" in tables:
        out["kb2"]["documents"] = db.one(
            "SELECT COUNT(*) c FROM kb2_knowledges k WHERE NOT EXISTS("
            "SELECT 1 FROM documents d WHERE d.id=k.id)")["c"]
    if "kb2_chunks" in tables:
        out["kb2"]["chunks"] = db.one(
            "SELECT COUNT(*) c FROM kb2_chunks c WHERE NOT EXISTS("
            "SELECT 1 FROM chunks u WHERE u.id=c.id)")["c"]
    for doc in db.q("SELECT id FROM documents WHERE doc_id IS NOT NULL "
                    "AND doc_id<>''"):
        if not db.one("SELECT 1 FROM chunks WHERE document_id=? LIMIT 1",
                      (doc["id"],)):
            out["pageindex"]["documents"] += 1
    return out


def run(dry_run=False):
    schema.init()
    if dry_run:
        return {"preview": preview(), "fts": 0}
    stats = {"kb2": migrate_kb2(), "pageindex": migrate_pageindex()}
    stats["fts"] = rebuild_fts()
    return stats


def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(CLI_HELP)
        return 0
    result = run(dry_run="--dry-run" in argv)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())