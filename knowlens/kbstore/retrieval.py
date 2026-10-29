# -*- coding: utf-8 -*-
"""Unified retrieval: keyword (FTS5 trigram) + structure expansion + optional vector.

Both parse modes share this module: page/section chunks and text chunks are
filtered by the same enabled/validity rules and return one citation shape.
"""
import math
import re
import time

from .. import db
from ..platform import services
from . import models

MAX_TOP_K = 30
RRF_K = 60


def today():
    return time.strftime("%Y-%m-%d")


def _live_clause(alias_d="d", alias_c="c"):
    return (f"{alias_c}.enabled=1 AND {alias_d}.enabled=1 "
            f"AND {alias_d}.deleted_at IS NULL "
            f"AND ({alias_d}.valid_from='' OR {alias_d}.valid_from<=?) "
            f"AND ({alias_d}.expires_at='' OR {alias_d}.expires_at>=?)")


def _kb_list(kb_ids):
    return [k for k in (services.as_list(kb_ids) or []) if k]


def _placeholders(items):
    return ",".join("?" * len(items))


def query_terms(query, limit=20):
    terms = []
    for run in re.findall(r"[一-鿿]+", query or ""):
        if len(run) <= 4:
            terms.append(run)
        else:
            terms.extend(run[i:i + 3] for i in range(len(run) - 2))
    terms.extend(re.findall(r"[0-9A-Za-z_]{2,}", query or ""))
    out = []
    for term in terms:
        if len(term) >= 3 and term not in out:
            out.append(term)
    return _spread(out, limit)


def _short_terms(query, limit=24):
    out = []
    for run in re.findall(r"[一-鿿]+", query or ""):
        for i in range(max(1, len(run) - 1)):
            frag = run[i:i + 2]
            if len(frag) == 2 and frag not in out:
                out.append(frag)
    return _spread(out, limit)


def _spread(items, limit):
    if len(items) <= limit:
        return items
    step = len(items) / float(limit)
    return [items[min(len(items) - 1, int(i * step))] for i in range(limit)]


def _fts(kb_ids, match, day, limit):
    sql = (
        "SELECT c.*, d.name AS doc_name, bm25(chunks_fts) AS rank "
        "FROM chunks_fts JOIN chunks c ON c.rid=chunks_fts.rowid "
        "JOIN documents d ON d.id=c.document_id "
        f"WHERE chunks_fts MATCH ? AND c.kb_id IN ({_placeholders(kb_ids)}) "
        f"AND {_live_clause()} AND c.chunk_type<>'parent_text' "
        "ORDER BY rank LIMIT ?")
    args = [match] + kb_ids + [day, day, limit]
    try:
        return db.q(sql, tuple(args))
    except Exception:
        return []


def _like(kb_ids, query, day, limit):
    like = f"%{query}%"
    sql = (
        "SELECT c.*, d.name AS doc_name, 0 AS rank "
        "FROM chunks c JOIN documents d ON d.id=c.document_id "
        f"WHERE c.kb_id IN ({_placeholders(kb_ids)}) AND {_live_clause()} "
        "AND c.chunk_type<>'parent_text' "
        "AND (c.content LIKE ? OR c.heading LIKE ?) LIMIT ?")
    args = kb_ids + [day, day, like, like, limit]
    return db.q(sql, tuple(args))


def _like_terms(kb_ids, terms, day, limit):
    if not terms:
        return []
    clause = " OR ".join("c.content LIKE ?" for _ in terms)
    sql = (
        "SELECT c.*, d.name AS doc_name, 0 AS rank "
        "FROM chunks c JOIN documents d ON d.id=c.document_id "
        f"WHERE c.kb_id IN ({_placeholders(kb_ids)}) AND {_live_clause()} "
        f"AND c.chunk_type<>'parent_text' AND ({clause}) LIMIT ?")
    args = kb_ids + [day, day] + [f"%{t}%" for t in terms] + [limit * 4]
    rows = db.q(sql, tuple(args))
    scored = []
    for row in rows:
        text = (row["content"] or "") + (row["heading"] or "")
        scored.append((sum(1 for t in terms if t in text), row))
    scored.sort(key=lambda item: -item[0])
    return [row for _, row in scored[:limit]]


def _keyword(kb_ids, query, day, limit):
    if len(query) >= 3:
        rows = _fts(kb_ids, '"' + query.replace('"', "") + '"', day, limit)
        if rows:
            return rows, "fts"
    terms = query_terms(query)
    if terms:
        rows = _fts(kb_ids, " OR ".join(f'"{t}"' for t in terms), day, limit)
        if rows:
            return rows, "fts"
    rows = _like(kb_ids, query, day, limit)
    if rows:
        return rows, "like"
    if len(query) <= 2:
        # Only two-character queries fall below the trigram index.
        return _like_terms(kb_ids, _short_terms(query), day, limit), "like"
    return [], "none"


# ────────────────────────── vector ──────────────────────────

def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _vector(kb_ids, query, day, limit):
    if not models.configured("embedding"):
        return []
    try:
        vectors = models.embed([query])
    except Exception:
        return []
    if not vectors:
        return []
    query_vec = vectors[0]
    sql = (
        "SELECT c.*, d.name AS doc_name, e.vector AS vector "
        "FROM chunk_embeddings e JOIN chunks c ON c.rid=e.rid "
        "JOIN documents d ON d.id=c.document_id "
        f"WHERE c.kb_id IN ({_placeholders(kb_ids)}) AND {_live_clause()} "
        "AND c.chunk_type<>'parent_text' LIMIT 500")
    rows = db.q(sql, tuple(kb_ids + [day, day]))
    scored = []
    for row in rows:
        vector = services.jload(row.pop("vector"), [])
        scored.append((_cosine(query_vec, vector), row))
    scored.sort(key=lambda item: -item[0])
    return [row for score, row in scored[:limit] if score > 0]


def index_embeddings(kb_id, document_id, chunks):
    """Best-effort embedding index for a document (no-op when unconfigured)."""
    if not models.configured("embedding") or not chunks:
        return 0
    try:
        vectors = []
        for start in range(0, len(chunks), 40):
            batch = chunks[start:start + 40]
            vectors.extend(models.embed([c["content"] for c in batch]))
    except Exception:
        return 0
    written = 0
    for chunk, vector in zip(chunks, vectors):
        row = db.one("SELECT rid FROM chunks WHERE id=?", (chunk["id"],))
        if not row:
            continue
        db.x("INSERT OR REPLACE INTO chunk_embeddings(rid,kb_id,document_id,"
             "model,dim,vector,created_at) VALUES(?,?,?,?,?,?,?)",
             (row["rid"], kb_id, document_id, "", len(vector),
              services.jdump(vector), services.now()))
        written += 1
    return written


def _rrf(lists, weights=None):
    weights = weights or [1.0] * len(lists)
    scores = {}
    for index, rows in enumerate(lists):
        for rank, row in enumerate(rows):
            key = row["id"]
            scores[key] = scores.get(key, 0.0) + \
                weights[index] / (RRF_K + rank + 1)
    return scores


# ────────────────────────── public ──────────────────────────

def search(kb_ids, query, top_k=8, day=None, use_vector=True,
           use_rerank=True):
    kb_ids = _kb_list(kb_ids)
    query = (query or "").strip()
    if not kb_ids or not query:
        return []
    day = day or today()
    limit = max(1, min(int(top_k or 8), MAX_TOP_K))
    keyword_rows, match_type = _keyword(kb_ids, query, day, limit * 2)
    lists, weights = [keyword_rows], [1.0]
    if use_vector:
        vector_rows = _vector(kb_ids, query, day, limit * 2)
        if vector_rows:
            lists.append(vector_rows)
            weights.append(1.0)
    if len(lists) > 1:
        scores = _rrf(lists, weights)
        rows = {row["id"]: row for rows in lists for row in rows}
        ordered = sorted(rows.values(), key=lambda r: -scores[r["id"]])
        match_type = "hybrid"
    else:
        ordered = keyword_rows
        scores = {row["id"]: 1.0 / (index + 1)
                  for index, row in enumerate(ordered)}
    ordered = _expand_parents(ordered[:limit * 2])
    if use_rerank and len(ordered) > 1 and models.configured("rerank"):
        ordered = _rerank(query, ordered)
    return [_public(row, scores.get(row["id"], 0.0), match_type)
            for row in ordered[:limit]]


def _expand_parents(rows):
    out = []
    seen = set()
    for row in rows:
        parent_id = row.get("parent_chunk_id")
        if parent_id:
            parent = db.one("SELECT content FROM chunks WHERE id=?",
                            (parent_id,))
            if parent:
                row = dict(row)
                row["parent_content"] = parent["content"]
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        out.append(row)
    return out


def _rerank(query, rows):
    try:
        results = models.rerank(query, [r["content"] for r in rows])
    except Exception:
        return rows
    order = [item.get("index") for item in results
             if isinstance(item, dict) and item.get("index") is not None]
    ranked = [rows[i] for i in order if 0 <= i < len(rows)]
    rest = [row for i, row in enumerate(rows) if i not in order]
    return ranked + rest


def _public(row, score, match_type):
    return {
        "kb_id": row["kb_id"], "document_id": row["document_id"],
        "doc": row.get("doc_name") or "", "chunk_id": row["id"],
        "chunk_type": row.get("chunk_type") or "text",
        "content": row.get("content") or "",
        "context": row.get("context_header") or "",
        "parent_content": row.get("parent_content") or "",
        "heading": row.get("heading") or "",
        "page": int(row.get("page") or 0),
        "page_end": int(row.get("page_end") or 0),
        "start_at": int(row.get("start_at") or 0),
        "end_at": int(row.get("end_at") or 0),
        "score": round(float(score), 6), "match_type": match_type,
    }


def delete_embeddings(document_id):
    db.x("DELETE FROM chunk_embeddings WHERE document_id=?", (document_id,))