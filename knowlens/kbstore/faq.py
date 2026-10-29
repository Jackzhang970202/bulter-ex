# -*- coding: utf-8 -*-
"""FAQ knowledge bases: Q/A entries stored as ``chunk_type='faq'`` chunks.

Mirrors the source project's FAQ type: entries carry standard / similar /
negative questions and one or more answers; retrieval scores questions with a
similarity threshold and filters negative examples.
"""
import csv
import difflib
import io
import json
import re

from .. import db
from ..platform import services

DEFAULT_THRESHOLD = 0.7
DEFAULT_MATCH_COUNT = 10
MAX_MATCH_COUNT = 50


def _container(kb_id):
    """One system document holds all FAQ entries of a knowledge base."""
    row = db.one("SELECT * FROM documents WHERE library_id=? AND "
                 "kind='faq' AND deleted_at IS NULL LIMIT 1", (kb_id,))
    if row:
        return row
    doc_id = services.uid("dc_faq_")
    db.x("INSERT INTO documents(id,space_id,library_id,name,kind,doc_id,"
         "status,pages,description,summary,source_path,error,image_count,"
         "vision,ocr_status,parse_mode,parse_config,file_hash,file_size,"
         "chars,chunks_count,content_revision,active_version_id,"
         "pending_version_id,version_no,validity_mode,valid_from,expires_at,"
         "enabled,folder_path,channel,tags,created_at,updated_at) "
         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
         "?,?,?,?)",
         (doc_id, "", kb_id, "FAQ 条目", "faq", "", "ready", 0, "", "", "",
          "", 0, 1, "", "faq", "{}", "", 0, 0, 0, 0, "", "", 1, "permanent",
          "", "", 1, "", "web", "[]", services.now(), services.now()))
    return db.one("SELECT * FROM documents WHERE id=?", (doc_id,))


def _metadata(row):
    return services.jload(row.get("metadata"), {})


def _public(row):
    meta = _metadata(row)
    return {
        "id": row["id"], "standard_question": meta.get("standard_question", ""),
        "similar_questions": meta.get("similar_questions", []),
        "negative_questions": meta.get("negative_questions", []),
        "answers": meta.get("answers", []),
        "answer_strategy": meta.get("answer_strategy", "all"),
        "tag_id": meta.get("tag_id", ""),
        "enabled": bool(row.get("enabled", 1)),
        "source": meta.get("source", ""),
        "revision": row.get("content_revision", 0),
    }


def list_entries(kb_id):
    rows = db.q("SELECT * FROM chunks WHERE kb_id=? AND chunk_type='faq' "
                "ORDER BY chunk_index", (kb_id,))
    return [_public(row) for row in rows]


def _write_entry(kb_id, entry, index=None):
    container = _container(kb_id)
    standard = (entry.get("standard_question") or "").strip()
    if not standard:
        raise ValueError("标准问不能为空")
    answers = [str(a).strip() for a in entry.get("answers") or [] if str(a).strip()]
    if not answers:
        raise ValueError("至少需要一个答案")
    similar = [str(q).strip() for q in entry.get("similar_questions") or []
               if str(q).strip()]
    negative = [str(q).strip() for q in entry.get("negative_questions") or []
                if str(q).strip()]
    meta = {"standard_question": standard, "similar_questions": similar,
            "negative_questions": negative, "answers": answers,
            "answer_strategy": entry.get("answer_strategy") or "all",
            "tag_id": entry.get("tag_id") or "",
            "source": entry.get("source") or "manual"}
    content = standard + "\n" + "\n".join(similar)
    metadata = json.dumps(meta, ensure_ascii=False)
    if index is None:
        index = (db.one("SELECT COALESCE(MAX(chunk_index),-1) m FROM chunks "
                        "WHERE kb_id=? AND chunk_type='faq'", (kb_id,))["m"]
                 + 1)
    cid = services.uid("faq_")
    rid = db.x(
        "INSERT INTO chunks(id,kb_id,document_id,chunk_index,chunk_type,"
        "content,source_content,context_header,heading,page,page_end,"
        "start_at,end_at,parent_chunk_id,enabled,flags,index_status,"
        "metadata,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, kb_id, container["id"], index, "faq", content,
         "", "", "", 0, 0, 0, 0, "", 1, 1, "ready", metadata,
         services.now(), services.now()))
    db.x("INSERT INTO chunks_fts(rowid,content,heading,context_header) "
         "VALUES(?,?,?,?)", (rid, content, "", ""))
    return _public(db.one("SELECT * FROM chunks WHERE id=?", (cid,)))


def create_entry(kb_id, entry):
    return _write_entry(kb_id, entry)


def update_entry(entry_id, entry):
    row = db.one("SELECT * FROM chunks WHERE id=? AND chunk_type='faq'",
                 (entry_id,))
    if not row:
        return None
    meta = _metadata(row)
    meta.update({key: entry[key] for key in
                 ("standard_question", "similar_questions", "answers",
                  "negative_questions", "answer_strategy", "tag_id")
                 if key in entry})
    content = (meta.get("standard_question") or "") + "\n" + \
        "\n".join(meta.get("similar_questions") or [])
    db.x("UPDATE chunks SET content=?,metadata=?,updated_at=? WHERE id=?",
         (content, json.dumps(meta, ensure_ascii=False), services.now(),
          entry_id))
    db.x("DELETE FROM chunks_fts WHERE rowid=?", (row["rid"],))
    if row.get("enabled", 1):
        db.x("INSERT INTO chunks_fts(rowid,content,heading,context_header) "
             "VALUES(?,?,?,?)", (row["rid"], content, "", ""))
    return _public(db.one("SELECT * FROM chunks WHERE id=?", (entry_id,)))


def delete_entry(entry_id):
    row = db.one("SELECT * FROM chunks WHERE id=? AND chunk_type='faq'",
                 (entry_id,))
    if not row:
        return False
    db.x("DELETE FROM chunks_fts WHERE rowid=?", (row["rid"],))
    db.x("DELETE FROM chunks WHERE id=?", (entry_id,))
    return True


def set_enabled(entry_id, enabled):
    row = db.one("SELECT * FROM chunks WHERE id=? AND chunk_type='faq'",
                 (entry_id,))
    if not row:
        return None
    db.x("UPDATE chunks SET enabled=?,updated_at=? WHERE id=?",
         (1 if enabled else 0, services.now(), entry_id))
    db.x("DELETE FROM chunks_fts WHERE rowid=?", (row["rid"],))
    if enabled:
        db.x("INSERT INTO chunks_fts(rowid,content,heading,context_header) "
             "VALUES(?,?,?,?)",
             (row["rid"], row["content"], "", ""))
    return _public(db.one("SELECT * FROM chunks WHERE id=?", (entry_id,)))


def _score(question, entry):
    candidates = [entry["standard_question"]] + entry["similar_questions"]
    best = 0.0
    for text in candidates:
        if not text:
            continue
        best = max(best, difflib.SequenceMatcher(
            None, question, text).ratio())
    return best


def search(kb_id, query, threshold=DEFAULT_THRESHOLD,
           match_count=DEFAULT_MATCH_COUNT):
    query = (query or "").strip()
    if not query:
        return []
    threshold = max(0.0, min(float(threshold), 1.0))
    match_count = max(1, min(int(match_count), MAX_MATCH_COUNT))
    results = []
    for entry in list_entries(kb_id):
        if not entry["enabled"]:
            continue
        if any(_score(query, {"standard_question": q, "similar_questions": []})
               >= threshold for q in entry["negative_questions"]):
            continue
        score = _score(query, entry)
        if score >= threshold:
            results.append((score, entry))
    results.sort(key=lambda pair: -pair[0])
    return [{"score": round(score, 4), **entry}
            for score, entry in results[:match_count]]


def import_entries(kb_id, entries, mode="append", dry_run=False):
    if mode not in ("append", "replace"):
        raise ValueError("mode 必须是 append 或 replace")
    existing = {e["standard_question"]: e for e in list_entries(kb_id)}
    created = merged = skipped = 0
    errors = []
    kept = set()
    for index, entry in enumerate(entries or []):
        standard = (entry.get("standard_question") or "").strip()
        if not standard:
            errors.append({"index": index, "error": "标准问不能为空"})
            skipped += 1
            continue
        if standard in existing:
            kept.add(standard)
            if dry_run:
                merged += 1
                continue
            current = existing[standard]
            similar = sorted(set(current["similar_questions"]
                                 + (entry.get("similar_questions") or [])))
            answers = entry.get("answers") or current["answers"]
            update_entry(current["id"], {
                "standard_question": standard,
                "similar_questions": similar, "answers": answers})
            merged += 1
            continue
        kept.add(standard)
        if dry_run:
            created += 1
            continue
        try:
            _write_entry(kb_id, entry)
            created += 1
        except ValueError as exc:
            errors.append({"index": index, "error": str(exc)})
            skipped += 1
    removed = 0
    if mode == "replace" and not dry_run:
        for standard, entry in existing.items():
            if standard not in kept:
                delete_entry(entry["id"])
                removed += 1
    return {"mode": mode, "dry_run": bool(dry_run), "created": created,
            "merged": merged, "skipped": skipped, "removed": removed,
            "errors": errors[:50]}


def export_entries(kb_id, fmt="json"):
    entries = list_entries(kb_id)
    if fmt == "json":
        return json.dumps(entries, ensure_ascii=False, indent=2), "json"
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["标准问", "相似问", "反例问", "答案", "标签"])
    for entry in entries:
        writer.writerow([entry["standard_question"],
                         "##".join(entry["similar_questions"]),
                         "##".join(entry["negative_questions"]),
                         "##".join(entry["answers"]), entry["tag_id"]])
    return buffer.getvalue(), "csv"