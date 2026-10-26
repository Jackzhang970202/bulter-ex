# -*- coding: utf-8 -*-
"""Five-stage Q&A pipeline: routing → retrieval navigation → sufficiency → synthesis verdict → citation validation.

② Reuses the agent tool loop (browse/structure/pages) of PageIndex own-model chat.
The doc_id allowlist is hard-enforced at the tool layer: cross-space access is impossible,
and libraries that were not selected are unreachable.
Events are pushed to the SSE layer via the emit callback.
"""
import json
import re
import time

from . import config, db, kb, llm

VERDICTS = ("能", "不能", "有条件", "未覆盖", "陈述", "操作", "对比")

SYS_BASE = (
    "你是部门知识问答助手，服务于{space_name}。只依据检索到的资料回答，"
    "禁止编造。所有引用用 <cite doc=\"文档名或ID\" page=\"N\"/> 标记。\n"
    "回答前先判断问题类型，【结论】一行必须以类型标记开头：\n"
    "1) 可行性判断题（“能不能/支不支持/能否做到X”）：\n"
    "【结论】【判定:能|不能|有条件|未覆盖】一句话判断；\n"
    "【依据】资料要点+引用；\n"
    "【方法或改进】（此类型必写）能→给出资料中的实现方法/步骤；"
    "不能或未覆盖→基于资料缺口给出改进建议，并标注“以下为基于现有资料的推断，非文档原文”；\n"
    "【缺失】还需补充的资料（无则写“无”）。\n"
    "2) 陈述/列举题（“做了什么/有哪些/讲讲X”）：\n"
    "【结论】【判定:陈述】直接给总括答案本身，不要出现“能/不能”字样；\n"
    "【依据】分条展开各要点并带引用；\n"
    "【缺失】（仅当材料不完整时写）。禁止输出【方法或改进】节。\n"
    "3) 操作/步骤题（“怎么用/如何操作/步骤是什么”）：\n"
    "【结论】【判定:操作】一句话概括操作路径；【依据】按操作顺序列步骤带引用；"
    "【缺失】（如适用）。禁止“能/不能”式开头。\n"
    "4) 对比/关系题（“X和Y什么关系/区别”）：\n"
    "【结论】【判定:对比】一句话对比结论；【依据】两侧各自带引用；"
    "【缺失】（如适用）。\n"
    "拿不准类型时，优先按陈述题处理——只有用户明确在问可行性时才使用能不能判定。\n"
)

ROUTER_PROMPT = (
    "你是检索路由器。根据用户问题从候选文档中挑选应检索的文档（宁多勿漏，"
    "但最多 {cap} 个）。同时把问题改写为 1-3 个检索子问题（补充同义词/英文缩写）。"
    "只输出 JSON：{{\"docs\": [doc_id...], \"queries\": [\"子问题\"...]}}\n"
)


def candidate_docs(space_id, library_ids, tag_ids, doc_ids=None):
    """Filter candidate documents by library/tag/document (including the description from PI meta)."""
    metas = {m["id"]: m for m in kb.list_metas(space_id)}
    rows = db.q(
        "SELECT * FROM documents WHERE space_id=? AND status='ready'",
        (space_id,))
    if library_ids:
        rows = [r for r in rows if r["library_id"] in set(library_ids)]
    if doc_ids:
        allowed = set(doc_ids)
        rows = [r for r in rows if r["doc_id"] in allowed]
    if tag_ids:
        tagged = {t["document_id"] for t in db.q(
            "SELECT document_id, tag_id FROM doc_tags")}
        rows = [r for r in rows if all(
            (r["id"], tid) in tagged for tid in tag_ids)]
    out = []
    for r in rows:
        m = metas.get(r["doc_id"]) or {}
        tags = [t["name"] for t in db.q(
            "SELECT t.name FROM tags t JOIN doc_tags dt ON dt.tag_id=t.id "
            "WHERE dt.document_id=?", (r["id"],))]
        out.append({"doc_id": r["doc_id"], "db_id": r["id"],
                    "name": r["name"], "library_id": r["library_id"],
                    "description": (m.get("description") or r["description"]
                                    or "")[:config.LIB_DESC_MAX],
                    "tags": tags})
    return out


MAX_ROUTER_DOCS = 60   # Max number of docs listed in the router prompt (prevents large libraries from blowing up the context)


def _rank_docs(question, docs):
    """Lightweight coarse ranking without vector retrieval: sort by overlap between question terms and (file name + description + tags)."""
    import re as _re
    q = question.lower()
    terms = set(_re.findall(r"[a-z0-9_]{2,}|[一-鿿]{2,4}", q))

    def score(d):
        text = (d["name"] + " " + d["description"] + " "
                + " ".join(d["tags"])).lower()
        return sum(1 for t in terms if t in text)
    return sorted(docs, key=score, reverse=True)


def route(space_id, question, docs, digest):
    """① Scope routing: the LLM picks retrieval targets based on document descriptions.

    When there are too many candidates, first coarse-rank by keyword overlap and truncate to MAX_ROUTER_DOCS,
    then let the LLM refine the selection (at most 8 docs).
    """
    if len(docs) <= 4:
        return {"docs": [d["doc_id"] for d in docs], "queries": [question]}
    pool = docs if len(docs) <= MAX_ROUTER_DOCS else \
        _rank_docs(question, docs)[:MAX_ROUTER_DOCS]
    catalog = "\n".join(
        f"- {d['doc_id']} | {d['name']} | 库:{d['library_id']} "
        f"| 标签:{','.join(d['tags']) or '-'} | {d['description']}"
        for d in pool)
    more = (f"\n（候选共 {len(docs)} 篇，以上为按相关度初筛的 "
            f"{len(pool)} 篇）") if len(pool) < len(docs) else ""
    messages = [{"role": "system",
                 "content": ROUTER_PROMPT.format(cap=min(8, len(pool)))},
                {"role": "user",
                 "content": f"部门背景：\n{digest[:2000]}\n\n候选文档：\n"
                            f"{catalog}{more}\n\n问题：{question}"}]
    obj, _ = llm.complete_json(messages, max_tokens=500)
    pool_ids = {x["doc_id"] for x in pool}
    chosen = [d for d in obj.get("docs", []) if d in pool_ids]
    if not chosen:
        chosen = [d["doc_id"] for d in pool[:8]]
    return {"docs": chosen[:8],
            "queries": obj.get("queries") or [question]}


# ────────────────────────── main flow ──────────────────────────

def answer(space_id, question, library_ids=None, tag_ids=None,
           history=None, digest="", on_event=None, mode="kb",
           temp_context="", temp_doc_ids=None, temp_docs=None,
           doc_ids=None, focus=None):
    """Execute one complete Q&A turn. history contains the visible messages of the existing conversation.

    mode="kb": scope = selected libraries in the space (+ tag filter), using the five-stage pipeline.
    mode="adhoc": scope = files temporarily uploaded in this session. Large files are already indexed (temp_doc_ids/temp_docs),
      small files are inlined (temp_context); if only inline material exists, answer directly.
    """
    emit = on_event or (lambda e: None)
    space = db.one("SELECT * FROM spaces WHERE id=?", (space_id,))
    if mode == "adhoc" and not temp_docs:
        return _answer_direct(space, question, history, temp_context,
                              digest, emit)
    docs = temp_docs if mode == "adhoc" else candidate_docs(
        space_id, library_ids, tag_ids, doc_ids)
    if not docs and not temp_context:
        emit({"type": "result", "verdict": "未覆盖",
              "answer": "当前范围内还没有可用的知识库文档。请先在库中上传并等待索引完成。",
              "citations": [], "missing": [], "route_docs": []})
        return {"verdict": "未覆盖", "answer": "", "citations": []}
    if not docs:
        docs = []

    if mode == "adhoc":
        plan = {"docs": [d["doc_id"] for d in docs], "queries": [question]}
    elif doc_ids or focus:
        emit({"type": "stage", "stage": "router",
              "detail": "按当前文档范围检索"})
        ordered = docs
        if focus and focus.get("document_id"):
            ordered = sorted(
                docs, key=lambda d: 0 if d["doc_id"] == focus["document_id"]
                else 1)
        plan = {"docs": [d["doc_id"] for d in ordered[:8]],
                "queries": [question]}
    else:
        emit({"type": "stage", "stage": "router", "detail": "范围路由中"})
        plan = route(space_id, question, docs, digest)
    picked = [d for d in docs if d["doc_id"] in plan["docs"]] or docs[:8]
    emit({"type": "route",
          "docs": [{"doc_id": d["doc_id"], "name": d["name"]} for d in picked],
          "queries": plan["queries"]})

    client = kb.get_client(space_id)
    sys_prompt = SYS_BASE.format(space_name=space["name"])
    if digest:
        sys_prompt += "\n\n部门认知（发行版，优先级低于用户当前问题中的明确指令）：\n" + digest
    if temp_context:
        sys_prompt += "\n\n本次会话临时资料：\n" + temp_context
    if focus:
        fdoc = next((d for d in docs
                     if d["doc_id"] == focus.get("document_id")), None)
        fname = (fdoc or {}).get("name") or focus.get("doc_name") or ""
        page = focus.get("page") or 0
        sel = (focus.get("selection_text") or "").strip()
        before = (focus.get("selection_before") or "").strip()[:300]
        after = (focus.get("selection_after") or "").strip()[:300]
        block = f"\n\n用户当前查看的上下文：\n文档：《{fname}》"
        if page:
            block += f" 第 {page} 页"
        if sel:
            block += (f"\n选中片段：「{sel[:500]}」\n上文：{before}\n下文：{after}"
                      "\n请优先围绕该片段回答；涉及任何数字或结论时，"
                      "说明其原文出处或计算方式。")
        sys_prompt += block

    instructions = sys_prompt
    messages = list(history or [])
    qtext = "\n".join(plan["queries"]) if plan["queries"] != [question] \
        else question
    if focus and focus.get("selection_text"):
        qtext += "\n关注片段：" + focus["selection_text"][:300]
    messages.append({"role": "user", "content":
                     f"检索重点：{qtext}\n原始问题：{question}"})

    emit({"type": "stage", "stage": "retrieve",
          "detail": f"检索导航中（{len(picked)} 个文档）"})
    t0 = time.time()
    full, thinking_len, tools, tool_outputs = _run_chat(
        client, messages, [d["doc_id"] for d in picked], instructions, emit)
    emit({"type": "stage", "stage": "synthesize",
          "detail": f"引用验证与结构化（检索耗时 {time.time()-t0:.0f}s）"})

    verdict = _detect_verdict(full)
    citations = _validate_citations(space_id, full, picked)
    answer_clean = _strip_cites(full)
    missing = _extract_section(full, "【缺失】")
    # The verdict type is already shown by the badge, so remove the duplicate raw verdict marker from the body
    answer_clean = re.sub(r'【结论】\s*【判定[:：][^】]*】', "【结论】", answer_clean)

    # ③ Sufficiency: when evidence is too scarce and the verdict is "not covered", broaden the scope and retrieve one more round
    if verdict == "未覆盖" and len(tools) <= 2 and len(docs) > len(picked):
        emit({"type": "stage", "stage": "retrieve",
              "detail": "证据不足，扩大范围补充检索"})
        extra = [d["doc_id"] for d in docs
                 if d["doc_id"] not in plan["docs"]][:8]
        if extra:
            full2, _, tools2, tool_outputs2 = _run_chat(
                client, messages, [d["doc_id"] for d in picked] + extra,
                instructions + "\n上一轮未找到足够证据，请扩大检索。", emit)
            if len(full2) > len(full):
                full = full2
                tool_outputs = tool_outputs2
                verdict = _detect_verdict(full)
                citations = _validate_citations(space_id, full, docs)
                answer_clean = _strip_cites(full)
                missing = _extract_section(full, "【缺失】")
                answer_clean = re.sub(
                    r'【结论】\s*【判定[:：][^】]*】', "【结论】", answer_clean)

    provenance = []
    if _needs_provenance(question, focus):
        emit({"type": "stage", "stage": "provenance",
              "detail": "数值与事实溯源核验中"})
        provenance = _build_provenance(space_id, question, answer_clean,
                                       picked, tool_outputs, citations, focus)

    result = {"verdict": verdict, "answer": answer_clean,
              "citations": citations, "missing": missing,
              "provenance": provenance,
              "route_docs": [d["doc_id"] for d in picked]}
    emit({"type": "result", **{k: v for k, v in result.items()
                               if k != "route_docs"},
          "route_docs": [d["doc_id"] for d in picked]})
    return result


def _needs_provenance(question, focus):
    if focus:
        return True
    q = question or ""
    return bool(re.search(r"\d", q)) or any(
        k in q for k in ("怎么来的", "来源", "出处", "依据", "怎么算",
                         "如何计算", "推导"))


def _collect_pages(obj, out, depth=0):
    """Recursively extract {doc, page, text} from pageindex tool outputs."""
    if depth > 3 or len(out) > 30:
        return
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith(("{", "[")):
            try:
                _collect_pages(json.loads(s), out, depth + 1)
            except Exception:
                pass
        return
    if isinstance(obj, dict):
        doc = obj.get("doc_name") or obj.get("document") or ""
        pages = obj.get("pages")
        if isinstance(pages, list):
            for p in pages:
                if not isinstance(p, dict):
                    continue
                md = p.get("markdown") or p.get("content") or p.get("text") or ""
                if isinstance(md, str) and md.strip():
                    out.append({"doc": doc,
                                "page": p.get("page_index") or p.get("page") or 0,
                                "text": md})
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _collect_pages(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _collect_pages(v, out, depth + 1)


PROVENANCE_PROMPT = (
    "你是资料溯源助手。只依据“证据”和“引用摘录”，对问题、回答以及选中片段中的"
    "关键数值或事实做溯源，禁止使用模型常识补充。\n"
    "规则：\n"
    "1) 数字直接出现在证据中 → status=direct，sources 给出文档名、页码、原文摘录；\n"
    "2) 数字由证据中的数值计算得出 → status=derived，给出 formula 和每个输入值的 source；\n"
    "3) 证据中找不到 → status=not_found，并在 missing 说明缺什么；\n"
    "4) sources.doc 必须是证据/引用中出现的文档名；page 必须是整数；\n"
    "5) 最多 5 个 targets；若选中片段不是数字，也要把该片段作为一个事实目标溯源。\n"
    "只输出 JSON：{{\"targets\":[{{\"value\":\"...\","
    "\"status\":\"direct|derived|not_found\","
    "\"sources\":[{{\"doc\":\"...\",\"page\":1,\"excerpt\":\"...\","
    "\"kind\":\"text|table|figure\"}}],"
    "\"derivation\":{{\"formula\":\"...\",\"inputs\":[{{\"value\":\"...\","
    "\"source\":\"...\"}}]}},\"missing\":[\"...\"]}}]}}"
)


def _resolve_source(space_id, picked, src):
    if not isinstance(src, dict):
        return None
    doc = str(src.get("doc") or "").strip()
    d = next((x for x in picked
              if x["name"] == doc or x["doc_id"] == doc), None)
    if not d:
        return None
    try:
        page = int(src.get("page") or 0)
    except (TypeError, ValueError):
        return None
    meta = kb.get_meta(space_id, d["doc_id"]) or {}
    if page < 1 or page > (meta.get("pageNum") or 0):
        return None
    return {"doc_id": d["doc_id"], "db_id": d.get("db_id", ""),
            "doc_name": d["name"], "page": page,
            "excerpt": str(src.get("excerpt") or "")[:400],
            "kind": str(src.get("kind") or "text")[:12]}


def _build_provenance(space_id, question, answer_text, picked,
                      tool_outputs, citations, focus):
    pages = []
    for out in tool_outputs or []:
        _collect_pages(out, pages)
    evidence = []
    used = 0
    for p in pages:
        if used >= 12000:
            break
        text = (p.get("text") or "")[:1500]
        evidence.append(f"[{p.get('doc') or '?'} p{p.get('page')}] {text}")
        used += len(text)
    for c in citations or []:
        evidence.append(f"[{c.get('doc_name')} p{c.get('page')}] "
                        f"{(c.get('excerpt') or '')[:800]}")
    catalog = "\n".join(f"- {d['name']} (doc_id={d['doc_id']})"
                        for d in picked)
    sel = ((focus or {}).get("selection_text") or "").strip()
    user = (f"文档目录：\n{catalog}\n\n证据：\n" + "\n\n".join(evidence) +
            f"\n\n问题：{question}\n\n回答：{answer_text[:4000]}")
    if sel:
        user += f"\n\n选中片段：{sel[:500]}"
    try:
        obj, _ = llm.complete_json(
            [{"role": "system", "content": PROVENANCE_PROMPT},
             {"role": "user", "content": user}], max_tokens=1600)
    except Exception:
        return []
    targets = []
    for t in (obj.get("targets") or [])[:5]:
        if not isinstance(t, dict):
            continue
        sources = []
        for s in (t.get("sources") or [])[:6]:
            r = _resolve_source(space_id, picked, s)
            if r:
                sources.append(r)
        status = str(t.get("status") or "").strip()
        if status not in ("direct", "derived", "not_found"):
            status = "direct" if sources else "not_found"
        if status in ("direct", "derived") and not sources:
            status = "not_found"
        deriv = {}
        if status == "derived":
            d = t.get("derivation") or {}
            deriv = {"formula": str(d.get("formula") or "")[:300],
                     "inputs": [{"value": str(x.get("value") or "")[:120],
                                 "source": str(x.get("source") or "")[:200]}
                                for x in (d.get("inputs") or [])[:6]
                                if isinstance(x, dict)]}
        targets.append({
            "value": str(t.get("value") or "")[:120],
            "status": status,
            "sources": sources,
            "derivation": deriv,
            "missing": [str(x)[:200] for x in (t.get("missing") or [])[:5]],
        })
    if not targets and sel:
        targets.append({"value": sel[:120], "status": "not_found",
                        "sources": [], "derivation": {},
                        "missing": ["未在资料中找到该片段的出处"]})
    return targets


def _run_chat(client, messages, doc_ids, instructions, emit):
    """Run one pageindex agent retrieval loop and forward its events.

    Returns (answer_text, thinking_len, tools, tool_outputs); tool_outputs keep
    the page contents returned by the tools for the provenance stage.
    """
    stream = client.chat(messages, doc_id=doc_ids, stream=True,
                         citations=True, instructions=instructions,
                         max_turns=config.MAX_TURNS)
    answer_parts, thinking_len, tools, tool_outputs = [], 0, [], []
    pending_calls = {}
    for ev in stream.events:
        t = ev.get("type")
        if t == "thinking":
            thinking_len += len(ev.get("delta", ""))
            emit({"type": "thinking", "delta": ev.get("delta", "")})
        elif t == "tool_call":
            tools.append(ev["name"])
            pending_calls[ev.get("call_id")] = ev
            emit({"type": "tool_call", "name": ev["name"],
                  "arguments": _clip(ev.get("arguments"))})
        elif t == "tool_result":
            tool_outputs.append(ev.get("output"))
            emit({"type": "tool_result", "name": ev.get("name", ""),
                  "output": _clip(ev.get("output"))})
        elif t == "answer":
            answer_parts.append(ev.get("delta", ""))
            emit({"type": "answer", "delta": ev.get("delta", "")})
    return "".join(answer_parts), thinking_len, tools, tool_outputs


def _clip(v, n=400):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s[:n] + ("…" if len(s) > n else "")


_CITE_RE = re.compile(r"<cite\s([^<>]*)>(?:</cite>)?")
_ATTR_RE = re.compile(r'\b(\w+)=["\'](.*?)["\']')


def _validate_citations(space_id, text, picked_docs):
    """⑤ Citation validation: check that each doc/page actually exists and drop hallucinated citations."""
    metas = {d["doc_id"]: d for d in picked_docs}
    names = {d["name"]: d["doc_id"] for d in picked_docs}
    found, seen = [], set()
    for m in _CITE_RE.finditer(text):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        doc, page = attrs.get("doc", ""), attrs.get("page", "")
        if not doc:
            continue
        doc_id = doc if doc in metas else names.get(doc)
        if not doc_id:
            continue
        try:
            p = int(str(page).split("-")[0])
        except ValueError:
            continue
        meta = kb.get_meta(space_id, doc_id) or {}
        if p < 1 or p > (meta.get("pageNum") or 0):
            continue
        key = (doc_id, p)
        if key in seen:
            continue
        seen.add(key)
        pg = kb.get_pages(space_id, doc_id, [p])
        excerpt = (pg[0]["markdown"][:300] if pg else "")
        found.append({"doc_id": doc_id, "doc_name": meta.get("name", doc),
                      "page": p, "excerpt": excerpt})
    return found


_VERDICT_MARK = re.compile(r"【判定[:：]\s*([^】\s]+)】")


def _detect_verdict(text):
    # Prefer the model's explicit verdict marker
    m = _VERDICT_MARK.search(text[:200])
    if m:
        v = m.group(1).strip()
        return v if v in VERDICTS else ("能" if "能" == v else "陈述")
    # Fallback: when no marker is present, use keyword heuristics; default to "statement" (instead of the old "conditional")
    # so descriptive answers are not mislabeled as verdicts
    head = text[:120].replace(" ", "")
    for v in VERDICTS:
        if f"【结论】{v}" in head:
            return v
    if re.search(r"(不能|无法|不支持|未实现|没有提供)", head):
        return "不能"
    if re.search(r"(能不能|能否|是否支持|可以做|能实现|已支持)", head):
        return "能"
    if "未覆盖" in head or "文档未" in head or "没有找到" in head:
        return "未覆盖"
    return "陈述"


def _extract_section(text, marker):
    idx = text.find(marker)
    if idx < 0:
        return ""
    tail = text[idx + len(marker):]
    for stop in ("【", "\n\n\n"):
        j = tail.find(stop)
        if j >= 0:
            tail = tail[:j]
    return _strip_cites(tail).strip()[:600]


def _strip_cites(text):
    return _CITE_RE.sub("", text)


ADHOC_DIRECT_SYS = (
    "你是资料分析助手。仅依据用户提供的临时资料回答问题，资料未覆盖处明确说明，"
    "不编造。可用简洁中文，需要时给步骤或改进建议。{digest}"
)


def _answer_direct(space, question, history, temp_context, digest, emit):
    """adhoc small-file direct answer: the material is already inlined, so no retrieval tool loop is needed."""
    emit({"type": "stage", "stage": "synthesize",
          "detail": f"基于临时资料直接回答（{len(temp_context)} 字符）"})
    sys_txt = ADHOC_DIRECT_SYS.format(
        digest=("\n部门背景：" + digest[:1500]) if digest else "")
    messages = [{"role": "system",
                 "content": sys_txt + "\n\n临时资料：\n" + temp_context}]
    messages += list(history or [])
    messages.append({"role": "user", "content": question})
    text, usage = llm.complete(messages, temperature=0.3, max_tokens=4000)
    emit({"type": "answer", "delta": text})
    result = {"verdict": "已回答", "answer": text, "citations": [],
              "missing": [], "route_docs": []}
    emit({"type": "result", **result})
    return result
