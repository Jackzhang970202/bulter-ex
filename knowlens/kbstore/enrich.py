# -*- coding: utf-8 -*-
"""Post-parse enrichment: summary, generated questions, auto tags, table summary.

Mirrors the source project's post-process fan-out, but runs as one background
pass per document. Every step is individually guarded: a failure is recorded
and never blocks the document from being searchable.
"""
import json
import re

from .. import db
from ..platform import llm_client, services
from . import models

MAX_SUMMARY_INPUT = 16000
MAX_SUMMARY_OUTPUT = 4096
MAX_QUESTION_CHUNKS = 200
MAX_TAG_CANDIDATES = 500
TAG_CONFIDENCE = 0.75

SUMMARY_SYSTEM = """你是精确的文档摘要助手。只依据用户提供的正文总结，不要根据文件名或扩展名猜测。
输出 100-500 字的客观摘要，覆盖文档的核心主题、关键结论与重要细节。
不要输出标题、前缀、解释或 Markdown 代码块。若正文确实没有可读内容，只输出：无可提取文本。"""

QUESTION_SYSTEM = """你是面向检索的问题生成助手。针对给定片段生成用户真正会搜索的问题。
要求：问题自包含（不用“它/该文档”），不超过 30 字，聚焦片段能完整回答的内容；
不要生成只能用一个词回答的问题。每行一个问题，不要编号、不要解释。"""

AUTO_TAG_SYSTEM = """你是文档分类助手。只从候选标签中选择最匹配的标签。
只输出 JSON：{"matches":[{"index":1,"confidence":0.0}]}，index 从 1 开始。
没有匹配时输出 {"matches":[]}。"""

TABLE_SYSTEM = """你是数据表说明助手。根据表头和样例行，用中文说明：
1) 这张表大致描述什么业务；2) 每一列的含义与用途；3) 可注意的数据特征。
不要编造具体数值，不要输出代码。"""


def _config(doc):
    kb = db.one("SELECT * FROM libraries WHERE id=?", (doc["library_id"],))
    kb_cfg = services.jload(kb.get("config") if kb else "{}", {})
    doc_cfg = services.jload(doc.get("parse_config"), {})
    merged = dict(kb_cfg.get("enrichment") or {})
    merged.update(doc_cfg.get("enrichment") or {})
    return kb, merged


def _llm_model(kb, cfg):
    model_id = cfg.get("model_id") or (kb or {}).get("model_id") or ""
    return models.resolve("llm", model_id).get("model_name") or ""


def _text_chunks(doc_id):
    return db.q("SELECT * FROM chunks WHERE document_id=? AND "
                "chunk_type IN ('text','page') AND enabled=1 "
                "ORDER BY chunk_index", (doc_id,))


def _sample(text, limit=MAX_SUMMARY_INPUT):
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    middle = int(limit * 0.2)
    tail = limit - head - middle
    mid_start = max(head, len(text) // 2 - middle // 2)
    return (text[:head] + "\n\n[...content omitted...]\n\n"
            + text[mid_start:mid_start + middle]
            + "\n\n[...content omitted...]\n\n" + text[-tail:])


def _complete(system, user, model, temperature=0.3, max_tokens=1200):
    result = llm_client.complete(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        model=model or None, temperature=temperature, max_tokens=max_tokens)
    return (result.get("text") or "").strip()


# ────────────────────────── summary ──────────────────────────

def summarize(doc_id, force=False):
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not doc:
        return None, "文档不存在"
    kb, cfg = _config(doc)
    if not cfg.get("summary") and not force:
        return None, "未启用摘要"
    model = _llm_model(kb, cfg)
    if not model:
        return None, "未配置摘要模型"
    chunks = _text_chunks(doc_id)
    if not chunks:
        db.x("UPDATE documents SET summary_status='failed',updated_at=? "
             "WHERE id=?", (services.now(), doc_id))
        return None, "没有可摘要的文本块"
    text = _sample("\n\n".join(c["content"] for c in chunks))
    summary = _complete(SUMMARY_SYSTEM, text, model, 0.3, 2048)
    summary = re.sub(r"^```.*?\n|```$", "", summary).strip()
    if len(summary) < 10:
        db.x("UPDATE documents SET summary_status='failed',updated_at=? "
             "WHERE id=?", (services.now(), doc_id))
        return None, "摘要为空"
    summary = summary[:MAX_SUMMARY_OUTPUT]
    db.x("UPDATE documents SET description=?,summary_status='completed',"
         "updated_at=? WHERE id=?",
         (summary, services.now(), doc_id))
    _upsert_summary_chunk(doc, summary, chunks[0])
    return summary, ""


def _upsert_summary_chunk(doc, summary, first_chunk):
    content = "# Summary\n\n" + summary
    row = db.one("SELECT * FROM chunks WHERE document_id=? AND "
                 "chunk_type='summary' LIMIT 1", (doc["id"],))
    if row:
        db.x("UPDATE chunks SET content=?,updated_at=? WHERE id=?",
             (content, services.now(), row["id"]))
        rid = row["rid"]
    else:
        rid = db.x(
            "INSERT INTO chunks(id,kb_id,document_id,chunk_index,chunk_type,"
            "content,source_content,context_header,heading,page,page_end,"
            "start_at,end_at,parent_chunk_id,enabled,flags,index_status,"
            "metadata,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (services.uid("ch_"), doc["library_id"], doc["id"], -1, "summary",
             content, "", "", "", 0, 0, 0, 0, first_chunk["id"], 1, 1,
             "ready", "{}", services.now(), services.now()))
    db.x("DELETE FROM chunks_fts WHERE rowid=?", (rid,))
    db.x("INSERT INTO chunks_fts(rowid,content,heading,context_header) "
         "VALUES(?,?,?,?)", (rid, content, "", ""))


# ────────────────────────── questions ──────────────────────────

def generate_questions(doc_id, count=3, force=False):
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not doc:
        return 0, "文档不存在"
    kb, cfg = _config(doc)
    questions_cfg = cfg.get("questions") or {}
    if not questions_cfg.get("enabled") and not force:
        return 0, "未启用问题生成"
    model = _llm_model(kb, cfg)
    if not model:
        return 0, "未配置模型"
    count = max(1, min(int(questions_cfg.get("count") or count or 3), 10))
    chunks = _text_chunks(doc_id)[:MAX_QUESTION_CHUNKS]
    total = 0
    for chunk in chunks:
        prompt = (f"文档名称：{doc['name']}\n\n片段：\n"
                  f"{chunk['content'][:3000]}\n\n请生成 {count} 个问题。")
        text = _complete(QUESTION_SYSTEM, prompt, model, 0.7, 512)
        questions = []
        for line in text.splitlines():
            line = re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s*", "", line).strip()
            if len(line) > 5:
                questions.append(line[:120])
            if len(questions) >= count:
                break
        if not questions:
            continue
        metadata = services.jload(chunk.get("metadata"), {})
        metadata["generated_questions"] = [
            {"id": f"q{index + 1}", "question": q,
             "content_revision": chunk.get("content_revision") or 0}
            for index, q in enumerate(questions)]
        db.x("UPDATE chunks SET metadata=?,updated_at=? WHERE id=?",
             (json.dumps(metadata, ensure_ascii=False), services.now(),
              chunk["id"]))
        _reindex_chunk(chunk, questions)
        total += len(questions)
    if total:
        db.x("UPDATE documents SET questions_count=?,updated_at=? WHERE id=?",
             (total, services.now(), doc_id))
    return total, ""


def _reindex_chunk(chunk, questions):
    text = chunk["content"]
    if questions:
        text += "\n" + "\n".join(questions)
    db.x("DELETE FROM chunks_fts WHERE rowid=?", (chunk["rid"],))
    if chunk.get("enabled", 1):
        db.x("INSERT INTO chunks_fts(rowid,content,heading,context_header) "
             "VALUES(?,?,?,?)",
             (chunk["rid"], text, chunk.get("heading") or "",
              chunk.get("context_header") or ""))


# ────────────────────────── auto tags ──────────────────────────

def auto_tag(doc_id, force=False):
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not doc:
        return [], "文档不存在"
    kb, cfg = _config(doc)
    tag_cfg = cfg.get("auto_tag") or {}
    if not tag_cfg.get("enabled") and not force:
        return [], "未启用自动标签"
    existing = [r["name"] for r in db.q(
        "SELECT t.name FROM document_tags d JOIN kb_tags t ON t.id=d.tag_id "
        "WHERE d.document_id=?", (doc_id,))]
    if existing and tag_cfg.get("skip_if_tagged", True):
        return [], "已有标签，跳过"
    candidates = db.q("SELECT id,name FROM kb_tags WHERE kb_id=? "
                      "ORDER BY sort_order,created_at DESC LIMIT ?",
                      (doc["library_id"], MAX_TAG_CANDIDATES))
    if not candidates:
        return [], "没有候选标签"
    model = _llm_model(kb, cfg)
    if not model:
        return [], "未配置模型"
    content = (f"文档名：{doc['name']}\n摘要：{doc.get('description') or ''}\n\n"
               + "\n".join(c["content"] for c in _text_chunks(doc_id))[:16000])
    listing = "\n".join(f"{i + 1}. {c['name']}"
                        for i, c in enumerate(candidates))
    prompt = f"候选标签：\n{listing}\n\n文档内容：\n<document>\n{content}\n</document>"
    raw = _complete(AUTO_TAG_SYSTEM, prompt, model, 0.1, 1024)
    parsed = llm_client.parse_json(raw) or {}
    matches = parsed.get("matches") if isinstance(parsed, dict) else []
    limit = max(1, min(int(tag_cfg.get("max_tags") or 3), 10))
    picked = []
    for item in matches or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index")) - 1
            confidence = float(item.get("confidence", 1))
        except (TypeError, ValueError):
            continue
        if confidence > 1:
            confidence = confidence / 100.0
        if confidence < TAG_CONFIDENCE:
            continue
        if 0 <= index < len(candidates):
            tag_id = candidates[index]["id"]
            if tag_id not in picked:
                picked.append(tag_id)
        if len(picked) >= limit:
            break
    for tag_id in picked:
        db.x("INSERT OR IGNORE INTO document_tags(document_id,tag_id) "
             "VALUES(?,?)", (doc_id, tag_id))
    return [c["name"] for c in candidates if c["id"] in picked], ""


# ────────────────────────── table summary ──────────────────────────

def table_summary(doc_id):
    """Deterministic profile chunk for CSV/XLSX (LLM optional)."""
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not doc:
        return None, "文档不存在"
    if (doc.get("kind") or "") not in ("csv", "xlsx", "xls"):
        return None, "不是表格文件"
    text = "\n".join(c["content"] for c in _text_chunks(doc_id))[:4000]
    if not text:
        return None, "没有表格内容"
    rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    header = rows[0] if rows else ""
    sample = "\n".join(rows[:6])
    kb, cfg = _config(doc)
    model = _llm_model(kb, cfg)
    description = ""
    if model and cfg.get("table_summary", True):
        description = _complete(
            TABLE_SYSTEM, f"表头：\n{header}\n\n样例行：\n{sample}",
            model, 0.3, 800)
    content = (f"# Table Summary\n\nTable name: {doc['name']}\n\n"
               + (description or f"表头：{header}\n样例行：\n{sample}"))
    content = content[:6000]
    row = db.one("SELECT * FROM chunks WHERE document_id=? AND "
                 "chunk_type='table_summary' LIMIT 1", (doc_id,))
    if row:
        db.x("UPDATE chunks SET content=?,updated_at=? WHERE id=?",
             (content, services.now(), row["id"]))
    else:
        rid = db.x(
            "INSERT INTO chunks(id,kb_id,document_id,chunk_index,chunk_type,"
            "content,source_content,context_header,heading,page,page_end,"
            "start_at,end_at,parent_chunk_id,enabled,flags,index_status,"
            "metadata,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (services.uid("ch_"), doc["library_id"], doc_id, -2,
             "table_summary", content, "", "", "", 0, 0, 0, 0, "", 1, 1,
             "ready", "{}", services.now(), services.now()))
        db.x("INSERT INTO chunks_fts(rowid,content,heading,context_header) "
             "VALUES(?,?,?,?)", (rid, content, "", ""))
    return content, ""


# ────────────────────────── orchestration ──────────────────────────

def enrich_document(doc_id, force=False):
    doc = db.one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not doc or doc.get("status") != "ready":
        return {"skipped": "not_ready"}
    kb, cfg = _config(doc)
    result = {}
    try:
        if cfg.get("summary") or force:
            summary, error = summarize(doc_id, force)
            result["summary"] = error or "ok"
    except Exception as exc:
        result["summary"] = f"{type(exc).__name__}: {exc}"
    try:
        if (cfg.get("questions") or {}).get("enabled") or force:
            count, error = generate_questions(doc_id, force=force)
            result["questions"] = error or count
    except Exception as exc:
        result["questions"] = f"{type(exc).__name__}: {exc}"
    try:
        if (cfg.get("auto_tag") or {}).get("enabled") or force:
            tags, error = auto_tag(doc_id, force)
            result["auto_tag"] = error or tags
    except Exception as exc:
        result["auto_tag"] = f"{type(exc).__name__}: {exc}"
    try:
        if (doc.get("kind") or "") in ("csv", "xlsx", "xls"):
            _, error = table_summary(doc_id)
            result["table"] = error or "ok"
    except Exception as exc:
        result["table"] = f"{type(exc).__name__}: {exc}"
    db.x("UPDATE documents SET enriched_at=?,updated_at=? WHERE id=?",
         (services.now(), services.now(), doc_id))
    return result