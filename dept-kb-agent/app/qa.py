# -*- coding: utf-8 -*-
"""问答五阶段管线：路由 → 检索导航 → 充分性 → 合成判定 → 引用验证。

② 复用 PageIndex own-model chat 的 agent 工具循环（browse/structure/pages），
doc_id 白名单在工具层硬约束：跨空间不可能、未选库不可达。
事件通过回调 emit 推给 SSE 层。
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


def candidate_docs(space_id, library_ids, tag_ids):
    """按库/标签过滤出候选文档（含 PI meta 的 description）。"""
    metas = {m["id"]: m for m in kb.list_metas(space_id)}
    rows = db.q(
        "SELECT * FROM documents WHERE space_id=? AND status='ready'",
        (space_id,))
    if library_ids:
        rows = [r for r in rows if r["library_id"] in set(library_ids)]
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


MAX_ROUTER_DOCS = 60   # 路由提示词里最多列出的文档数（防大库撑爆上下文）


def _rank_docs(question, docs):
    """无向量检索的轻量粗排：按问题词与(文件名+描述+标签)的重合度排序。"""
    import re as _re
    q = question.lower()
    terms = set(_re.findall(r"[a-z0-9_]{2,}|[一-鿿]{2,4}", q))

    def score(d):
        text = (d["name"] + " " + d["description"] + " "
                + " ".join(d["tags"])).lower()
        return sum(1 for t in terms if t in text)
    return sorted(docs, key=score, reverse=True)


def route(space_id, question, docs, digest):
    """① 范围路由：LLM 依据文档描述挑选检索目标。

    候选过多时先按关键词重合度粗排截断到 MAX_ROUTER_DOCS，
    再交给 LLM 精选（最多 8 篇）。
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


# ────────────────────────── 主流程 ──────────────────────────

def answer(space_id, question, library_ids=None, tag_ids=None,
           history=None, digest="", on_event=None, mode="kb",
           temp_context="", temp_doc_ids=None, temp_docs=None):
    """执行一次完整问答。history 为既有会话的可见消息。

    mode="kb"：范围=空间内选定库（+标签过滤），走五阶段管线。
    mode="adhoc"：范围=本会话临时上传。大文件已索引（temp_doc_ids/temp_docs），
      小文件内联（temp_context）；若只有内联材料则直答。
    """
    emit = on_event or (lambda e: None)
    space = db.one("SELECT * FROM spaces WHERE id=?", (space_id,))
    if mode == "adhoc" and not temp_docs:
        return _answer_direct(space, question, history, temp_context,
                              digest, emit)
    docs = temp_docs if mode == "adhoc" else candidate_docs(
        space_id, library_ids, tag_ids)
    if not docs and not temp_context:
        emit({"type": "result", "verdict": "未覆盖",
              "answer": "当前范围内还没有可用的知识库文档。请先在库中上传并等待索引完成。",
              "citations": [], "missing": [], "route_docs": []})
        return {"verdict": "未覆盖", "answer": "", "citations": []}
    if not docs:
        docs = []

    if mode == "adhoc":
        plan = {"docs": [d["doc_id"] for d in docs], "queries": [question]}
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

    instructions = sys_prompt
    messages = list(history or [])
    qtext = "\n".join(plan["queries"]) if plan["queries"] != [question] \
        else question
    messages.append({"role": "user", "content":
                     f"检索重点：{qtext}\n原始问题：{question}"})

    emit({"type": "stage", "stage": "retrieve",
          "detail": f"检索导航中（{len(picked)} 个文档）"})
    t0 = time.time()
    full, thinking_len, tools = _run_chat(
        client, messages, [d["doc_id"] for d in picked], instructions, emit)
    emit({"type": "stage", "stage": "synthesize",
          "detail": f"引用验证与结构化（检索耗时 {time.time()-t0:.0f}s）"})

    verdict = _detect_verdict(full)
    citations = _validate_citations(space_id, full, picked)
    answer_clean = _strip_cites(full)
    missing = _extract_section(full, "【缺失】")
    # 判定类型已由徽章呈现，正文里去掉重复的 【判定:X】 原始标记
    answer_clean = re.sub(r'【结论】\s*【判定[:：][^】]*】', "【结论】", answer_clean)

    # ③ 充分性：证据过少且判定为“未覆盖”时，扩范围补一轮
    if verdict == "未覆盖" and len(tools) <= 2 and len(docs) > len(picked):
        emit({"type": "stage", "stage": "retrieve",
              "detail": "证据不足，扩大范围补充检索"})
        extra = [d["doc_id"] for d in docs
                 if d["doc_id"] not in plan["docs"]][:8]
        if extra:
            full2, _, tools2 = _run_chat(
                client, messages, [d["doc_id"] for d in picked] + extra,
                instructions + "\n上一轮未找到足够证据，请扩大检索。", emit)
            if len(full2) > len(full):
                full = full2
                verdict = _detect_verdict(full)
                citations = _validate_citations(space_id, full, docs)
                answer_clean = _strip_cites(full)
                missing = _extract_section(full, "【缺失】")
                answer_clean = re.sub(
                    r'【结论】\s*【判定[:：][^】]*】', "【结论】", answer_clean)

    result = {"verdict": verdict, "answer": answer_clean,
              "citations": citations, "missing": missing,
              "route_docs": [d["doc_id"] for d in picked]}
    emit({"type": "result", **{k: v for k, v in result.items()
                               if k != "route_docs"},
          "route_docs": [d["doc_id"] for d in picked]})
    return result


def _run_chat(client, messages, doc_ids, instructions, emit):
    """跑一次 pageindex agent 检索循环，事件转推。返回 (answer_text, thinking_len, tools)。"""
    stream = client.chat(messages, doc_id=doc_ids, stream=True,
                         citations=True, instructions=instructions,
                         max_turns=config.MAX_TURNS)
    answer_parts, thinking_len, tools = [], 0, []
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
            emit({"type": "tool_result", "name": ev.get("name", ""),
                  "output": _clip(ev.get("output"))})
        elif t == "answer":
            answer_parts.append(ev.get("delta", ""))
            emit({"type": "answer", "delta": ev.get("delta", "")})
    return "".join(answer_parts), thinking_len, tools


def _clip(v, n=400):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s[:n] + ("…" if len(s) > n else "")


_CITE_RE = re.compile(r"<cite\s([^<>]*)>(?:</cite>)?")
_ATTR_RE = re.compile(r'\b(\w+)=["\'](.*?)["\']')


def _validate_citations(space_id, text, picked_docs):
    """⑤ 引用验证：逐条检查 doc/page 真实存在，剔除幻觉引用。"""
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
    # 首选模型显式输出的 【判定:X】 标记
    m = _VERDICT_MARK.search(text[:200])
    if m:
        v = m.group(1).strip()
        return v if v in VERDICTS else ("能" if "能" == v else "陈述")
    # 兜底：无标记时按关键词启发式，默认落到"陈述"（而非旧的"有条件"，
    # 避免把陈述型回答误标成判定）
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
    """adhoc 小文件直答：材料已内联，无需检索工具循环。"""
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
