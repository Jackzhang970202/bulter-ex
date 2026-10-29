# -*- coding: utf-8 -*-
"""Deterministic text duplicate detection.

Ported parameters from Knowledge Hub: NFKC + lowercase + whitespace removal,
K=13 character shingles, Winnowing window 8, MinHash 128, candidate recall
Jaccard >= 0.4 or bidirectional coverage >= 0.6.

Classification: content_duplicate / new_is_excerpt / new_version /
highly_similar / suspected_duplicate. Relations are undirected and stored with
a canonical direction (smaller document id first).
"""
import hashlib
import json
import re
import time
import unicodedata

from .. import db
from ..platform import services

K = 13
WINDOW = 8
MINHASH_DIM = 128
MIN_CANDIDATES = 20
RECALL_JACCARD = 0.4
RECALL_COVERAGE = 0.6
ALGO_VERSION = "v3"


def normalize(text):
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"\s+", "", text)


def _fnv1a(value):
    digest = hashlib.md5(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _shingles(text, k=K):
    if len(text) < k:
        return set()
    return {_fnv1a(text[i:i + k]) for i in range(len(text) - k + 1)}


def _winnow(shingles_sorted, window=WINDOW):
    if not shingles_sorted:
        return set()
    if len(shingles_sorted) <= window:
        return {min(shingles_sorted)}
    out = set()
    for i in range(len(shingles_sorted) - window + 1):
        out.add(min(shingles_sorted[i:i + window]))
    return out


def _minhash(shingles):
    if not shingles:
        return []
    values = list(shingles)[:5000]
    signature = []
    for seed in range(MINHASH_DIM):
        signature.append(min(((value ^ (seed * 0x9E3779B97F4A7C15)) & 0xFFFFFFFFFFFFFFFF)
                             for value in values))
    return signature


def fingerprint_text(text):
    normalized = normalize(text)
    shingles = _shingles(normalized)
    exact = _winnow(sorted(shingles))
    return {
        "normalized": normalized,
        "char_count": len(normalized),
        "shingles": shingles,
        "exact": exact,
        "minhash": _minhash(shingles),
    }


def _jaccard(a, b):
    if not a and not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def compare(a, b):
    """Return coverage/jaccard/classification for two fingerprints."""
    inter = len(a["exact"] & b["exact"])
    new_cov = inter / float(len(a["exact"])) if a["exact"] else 0.0
    old_cov = inter / float(len(b["exact"])) if b["exact"] else 0.0
    jac = inter / float(len(a["exact"] | b["exact"])) if (a["exact"] or b["exact"]) else 0.0
    if new_cov >= 0.98 and old_cov >= 0.98:
        kind = "content_duplicate"
    elif new_cov >= 0.95 and old_cov < 0.80:
        kind = "new_is_excerpt"
    elif new_cov < 0.80 and old_cov >= 0.95:
        kind = "new_version"
    elif new_cov >= 0.80 and old_cov >= 0.80:
        kind = "highly_similar"
    elif max(new_cov, old_cov) >= 0.60:
        kind = "suspected_duplicate"
    else:
        kind = ""
    return {"new_coverage": round(new_cov, 4),
            "old_coverage": round(old_cov, 4),
            "jaccard": round(jac, 4), "kind": kind}


def _text_for(doc_id):
    rows = db.q("SELECT content FROM chunks WHERE document_id=? AND "
                "chunk_type IN ('text','page') AND enabled=1 "
                "ORDER BY chunk_index", (doc_id,))
    return "\n".join(row["content"] for row in rows)


def _evidence(a_norm, b_norm, limit=3):
    """Shared K-grams with surrounding context (normalized text)."""
    out, seen = [], set()
    if not a_norm or not b_norm:
        return out
    for i in range(0, max(1, len(a_norm) - K + 1)):
        gram = a_norm[i:i + K]
        if gram in seen:
            continue
        pos = b_norm.find(gram)
        if pos < 0:
            continue
        seen.add(gram)
        out.append({
            "text": a_norm[max(0, i - 40):i + K + 40],
            "other": b_norm[max(0, pos - 40):pos + K + 40],
            "position": i,
        })
        if len(out) >= limit:
            break
    return out


def _save_fingerprint(doc_id, fp):
    db.x("INSERT OR REPLACE INTO document_fingerprints(document_id,"
         "normalized_hash,char_count,minhash,shingles,algo_version,"
         "created_at) VALUES(?,?,?,?,?,?,?)",
         (doc_id, hashlib.md5(fp["normalized"].encode("utf-8")).hexdigest(),
          fp["char_count"],
          services.jdump(fp["minhash"]),
          services.jdump(sorted(fp["exact"])[:20000]),
          ALGO_VERSION, services.now()))


def _load_fingerprints(kb_id, exclude=""):
    rows = db.q("SELECT f.*, d.name AS doc_name FROM document_fingerprints f "
                "JOIN documents d ON d.id=f.document_id "
                "WHERE d.library_id=? AND d.deleted_at IS NULL "
                "AND f.document_id<>? AND f.algo_version=?",
                (kb_id, exclude, ALGO_VERSION))
    out = []
    for row in rows:
        out.append({
            "document_id": row["document_id"], "name": row["doc_name"],
            "normalized": "", "char_count": row["char_count"],
            "exact": set(services.jload(row["shingles"], [])),
            "minhash": services.jload(row["minhash"], []),
        })
    return out


def scan_document(doc_id, limit=MIN_CANDIDATES):
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not doc:
        return {"error": "文档不存在"}
    text = _text_for(doc_id)
    fp = fingerprint_text(text)
    if not fp["exact"]:
        return {"document_id": doc_id, "relations": 0,
                "note": "没有足够文本"}
    _save_fingerprint(doc_id, fp)
    candidates = _load_fingerprints(doc["library_id"], doc_id)
    scored = []
    for item in candidates:
        jac = _jaccard(set(fp["minhash"]), set(item["minhash"])) \
            if fp["minhash"] and item["minhash"] else 0
        if doc_id <= item["document_id"]:
            result = compare(fp, item)
        else:
            # Canonical direction: the smaller document id is treated as "a".
            result = compare({"exact": item["exact"]}, fp)
        if result["kind"] or result["jaccard"] >= RECALL_JACCARD \
                or max(result["new_coverage"], result["old_coverage"]) >= RECALL_COVERAGE:
            scored.append((result, item))
    scored.sort(key=lambda pair: (-pair[0]["new_coverage"],
                                  pair[1]["document_id"]))
    created = 0
    for result, item in scored[:limit]:
        if not result["kind"]:
            continue
        source, target = sorted([doc_id, item["document_id"]])
        evidence = _evidence(fp["normalized"],
                             normalize(_text_for(item["document_id"])))
        relation_id = "dr_" + hashlib.md5(
            f"{source}:{target}".encode("utf-8")).hexdigest()[:16]
        db.x("INSERT OR REPLACE INTO duplicate_relations(id,kb_id,"
             "document_id,other_id,kind,coverage,jaccard,evidence,status,"
             "created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
             (relation_id, doc["library_id"], source, target,
              result["kind"],
              max(result["new_coverage"], result["old_coverage"]),
              result["jaccard"],
              json.dumps(evidence, ensure_ascii=False), "open",
              services.now()))
        created += 1
    return {"document_id": doc_id, "relations": created,
            "candidates": len(scored)}


def scan_kb(kb_id):
    docs = db.q("SELECT id FROM documents WHERE library_id=? AND "
                "deleted_at IS NULL", (kb_id,))
    total = 0
    for doc in docs:
        total += scan_document(doc["id"]).get("relations", 0)
    return {"documents": len(docs), "relations": total}


def list_relations(kb_id):
    return db.q("SELECT r.*, a.name AS source_name, b.name AS target_name "
                "FROM duplicate_relations r "
                "LEFT JOIN documents a ON a.id=r.document_id "
                "LEFT JOIN documents b ON b.id=r.other_id "
                "WHERE r.kb_id=? ORDER BY r.coverage DESC", (kb_id,))


def resolve(relation_id, status="ignored"):
    if status not in ("ignored", "confirmed_duplicate", "open"):
        raise ValueError("状态不合法")
    db.x("UPDATE duplicate_relations SET status=? WHERE id=?",
         (status, relation_id))
    return db.one("SELECT * FROM duplicate_relations WHERE id=?",
                  (relation_id,))