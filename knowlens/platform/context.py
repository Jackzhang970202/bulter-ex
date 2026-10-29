# -*- coding: utf-8 -*-
"""Unified knowledge context.

One retrieval surface for every question module (agent / expert / team /
workflow / Excel). Two knowledge backends are supported side by side:

* classic space  — PageIndex pages inside a ``space_id`` (existing product path)
* chunk base     — ``kb2`` bases (SQLite + FTS5), fine-grained chunks

``retrieve`` merges both, ``plan`` decides whether an Excel table and a
knowledge base should be used together, and ``render`` turns hits into the
prompt block used by every runtime.
"""
import re

from .. import kb2
from . import services, tools

MAX_BLOCK_CHARS = 6000

# 数值/表格型问法：更适合走 Excel 代码分析
DATA_WORDS = (
    "统计", "多少", "多少个", "几名", "几条", "排名", "排序", "最高", "最低",
    "最大", "最小", "趋势", "环比", "同比", "占比", "比例", "平均", "均值",
    "求和", "汇总", "合计", "总额", "总计", "增长", "下降", "分布", "对比",
    "差额", "前几", "top", "明细", "列出", "算出", "计算", "销售额", "利润",
    "成本", "数量", "金额", "单价", "毛利率", "增长率", "汇总表",
)
# 制度/口径型问法：更适合走知识库
POLICY_WORDS = (
    "规定", "制度", "流程", "标准", "要求", "条件", "审批", "依据", "原则",
    "政策", "办法", "规范", "细则", "口径", "定义", "含义", "职责", "权限",
    "是谁", "什么时候", "如何", "怎么办", "为什么", "能否", "是否", "可以吗",
)


def _hit_count(words, text):
    low = (text or "").lower()
    return sum(1 for w in words if w.lower() in low)


def classify(question):
    """Heuristic intent: data-driven, policy-driven, or both."""
    data = _hit_count(DATA_WORDS, question)
    policy = _hit_count(POLICY_WORDS, question)
    if data and not policy:
        return "data"
    if policy and not data:
        return "policy"
    if data and policy:
        return "mixed"
    return "unknown"


def plan(question, kb_ids=None, excel_ids=None):
    """Decide which sources to use for this question.

    Both attached → both are used (table for numbers, KB for definitions and
    citations); this is the "自适应的选择" contract: never silently drop one.
    """
    has_kb = bool(services.as_list(kb_ids))
    has_excel = bool(services.as_list(excel_ids))
    intent = classify(question)
    if has_kb and has_excel:
        mode = "both"
    elif has_kb:
        mode = "kb"
    elif has_excel:
        mode = "excel"
    else:
        mode = "none"
    return {"mode": mode, "use_kb": has_kb, "use_excel": has_excel,
            "intent": intent,
            "reason": {
                "both": "同时挂载知识库与表格：表格出数、知识库出定义与依据",
                "kb": "仅挂载知识库",
                "excel": "仅上传表格",
                "none": "没有挂载可检索的资料来源",
            }[mode]}


# ────────────────────────── retrieval ──────────────────────────

def kb_hits(question, kb_ids, limit=6):
    """Search chunk bases; results keep base/doc/page for citation."""
    hits = []
    ids = services.as_list(kb_ids)
    per_base = max(2, limit)
    for bid in ids:
        try:
            base = kb2.engine.stats(bid)
        except Exception:
            base = None
        if not base:
            continue
        name = base["base"].get("name") or bid
        for row in kb2.engine.search(bid, question, top_k=per_base):
            hits.append({"source": "kb2", "kb_id": bid, "base": name,
                         "doc": row.get("doc") or "", "heading": row.get("heading") or "",
                         "page": row.get("page") or 0,
                         "content": row.get("content") or "",
                         "chunk_id": row.get("chunk_id") or "",
                         "score": float(row.get("score") or 0)})
    return hits


def space_hits(question, space_id, library_ids=None, limit=6):
    """Classic PageIndex retrieval, normalised to the same shape."""
    if not space_id:
        return []
    try:
        result = tools.execute("knowledge_search", {
            "query": question, "top_k": min(limit, 8),
        }, {"space_id": space_id,
            "library_ids": services.as_list(library_ids)})
    except Exception:
        return []
    out = []
    for item in (result or {}).get("results") or []:
        out.append({"source": "space", "kb_id": space_id,
                    "base": "知识空间", "doc": item.get("doc") or "",
                    "heading": "", "page": item.get("page") or 0,
                    "content": item.get("excerpt") or "",
                    "chunk_id": "", "score": 1.0})
    return out


def _dedupe(hits):
    seen, out = set(), []
    for hit in hits:
        key = (hit.get("doc"), (hit.get("content") or "")[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def retrieve(question, kb_ids=None, space_id="", library_ids=None, limit=6):
    """Merged hits from both backends.

    Sources are interleaved rather than score-sorted: chunk scores come from
    bm25 and page scores come from term overlap, so the two scales are not
    comparable. Interleaving guarantees both backends reach the prompt.
    """
    chunks = kb_hits(question, kb_ids, limit)
    pages = space_hits(question, space_id, library_ids, limit)
    merged = []
    for index in range(max(len(chunks), len(pages))):
        if index < len(chunks):
            merged.append(chunks[index])
        if index < len(pages):
            merged.append(pages[index])
    return _dedupe(merged)[:max(1, limit * 2)]


def cite(hit):
    bits = [hit.get("doc") or "来源"]
    if hit.get("heading"):
        bits.append(hit["heading"])
    if hit.get("page"):
        bits.append(f"第{hit['page']}页")
    if hit.get("base") and hit.get("source") == "kb2":
        bits.append(hit["base"])
    return " ".join(bits)


def render(hits, budget=MAX_BLOCK_CHARS):
    """Prompt text for a set of hits, plus the hits that fit."""
    lines, kept, used = [], [], 0
    for hit in hits:
        block = f"[{cite(hit)}] {(hit.get('content') or '').strip()}"
        if used + len(block) > budget:
            continue
        lines.append(block)
        kept.append(hit)
        used += len(block)
    return kept, "\n\n".join(lines)


def citations(hits):
    return [{"doc": h.get("doc") or "", "base": h.get("base") or "",
             "heading": h.get("heading") or "", "page": h.get("page") or 0,
             "excerpt": re.sub(r"\s+", " ", h.get("content") or "")[:200]}
            for h in hits]


def build(question, *, kb_ids=None, space_id="", library_ids=None, limit=6):
    """Convenience: retrieve + render in one call."""
    hits = retrieve(question, kb_ids, space_id, library_ids, limit)
    return render(hits)