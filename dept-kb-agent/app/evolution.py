# -*- coding: utf-8 -*-
"""自我演进引擎：候选 → 审核 → 规则/画像 → 发行版 digest → 注入问答。

发行版是纯数据（markdown），由服务端在每次问答时拼装进系统提示词；
版本化 + 体积红线（超限拒渲保旧版）+ 可回滚。
"""
import json

from . import config, db, llm

COGNITIVE_PROMPT = (
    "你是部门知识管理员。基于以下材料（库摘要、近期问答与修正、既有认知），"
    "更新一份『部门认知』摘要，供问答助手作为背景知识。要求：\n"
    "1) 术语表：本部门特有的系统名/缩写及其一句话说明；\n"
    "2) 系统能力登记：已被会议/文档确认的系统能力与限制（注明出处库/文档）；\n"
    "3) 关注点与习惯：部门常问的问题类型、决策偏好、汇报口径；\n"
    "4) 只保留有材料支撑的内容，矛盾时以更新的证据为准，并丢弃过期条目。\n"
    "只输出 JSON：{\"glossary\": [\"术语 — 说明\"], \"capabilities\": "
    "[\"系统：能力陈述（出处）\"], \"habits\": [\"陈述\"]}"
)


def current_digest(space_id):
    row = db.one("SELECT * FROM digests WHERE space_id=? AND active=1 "
                 "ORDER BY ver DESC LIMIT 1", (space_id,))
    return row or None


def get_digest_text(space_id):
    d = current_digest(space_id)
    return d["content"] if d else ""


def build_digest_content(space_id):
    """从规则 + 认知画像 + 库摘要渲染 digest markdown。"""
    space = db.one("SELECT * FROM spaces WHERE id=?", (space_id,))
    ver = ((db.one("SELECT MAX(ver) AS v FROM digests WHERE space_id=?",
                   (space_id,)) or {"v": 0})["v"] or 0) + 1
    rules = db.q("SELECT * FROM rules WHERE space_id=? AND status='active' "
                 "ORDER BY id", (space_id,))
    lib_rows = db.q("SELECT * FROM libraries WHERE space_id=?", (space_id,))
    cog = db.one("SELECT * FROM profiles WHERE space_id=? AND name='cognitive' "
                 "ORDER BY updated_at DESC LIMIT 1", (space_id,))
    parts = [f"# 部门认知发行版 · {space['name']} · v{ver}",
             f"<!-- generated {db.now()} -->"]
    if rules:
        parts.append("\n## 部门规则（经人工审核）")
        for r in rules:
            line = f"- [R{r['id']}] {r['statement']}"
            if r["ex_good"] or r["ex_bad"]:
                line += f"（例 ✗{r['ex_bad']} ✓{r['ex_good']}）"
            parts.append(line)
    if cog and (cog["content"] or "").strip():
        obj = json.loads(cog["content"])
        if obj.get("glossary"):
            parts.append("\n## 术语与系统")
            parts += [f"- {g}" for g in obj["glossary"][:30]]
        if obj.get("capabilities"):
            parts.append("\n## 系统能力登记（会议/文档确认）")
            parts += [f"- {c}" for c in obj["capabilities"][:30]]
        if obj.get("habits"):
            parts.append("\n## 部门关注点与习惯")
            parts += [f"- {h}" for h in obj["habits"][:20]]
    summaries = [l for l in lib_rows if (l.get("summary") or "").strip()]
    if summaries:
        parts.append("\n## 当前知识库")
        for l in summaries:
            n = db.one("SELECT COUNT(*) AS c FROM documents WHERE library_id=?"
                       " AND status='ready'", (l["id"],))["c"]
            parts.append(f"- 库「{l['name']}」（{n} 篇）：{l['summary']}")
    body = "\n".join(parts)
    return body, ver


def render(space_id):
    """渲染并保存新版本；超体积红线拒渲保旧版。"""
    import time
    import uuid
    body, ver = build_digest_content(space_id)
    nbytes = len(body.encode("utf-8"))
    if nbytes > config.DIGEST_MAX_BYTES:
        db.log_event(space_id, None, "digest_rejected",
                     {"bytes": nbytes, "ver": ver})
        return None
    db.x("UPDATE digests SET active=0 WHERE space_id=?", (space_id,))
    did = uuid.uuid4().hex[:12]
    nonce = uuid.uuid4().hex[:8]
    db.x("INSERT INTO digests(id,space_id,ver,bytes,nonce,content,active,"
         "created_at) VALUES(?,?,?,?,?,?,1,?)",
         (did, space_id, ver, nbytes, nonce, body, db.now()))
    db.log_event(space_id, None, "digest_rendered",
                 {"ver": ver, "bytes": nbytes, "nonce": nonce})
    return {"id": did, "ver": ver, "bytes": nbytes, "nonce": nonce}


def rollback(space_id, digest_id):
    db.x("UPDATE digests SET active=0 WHERE space_id=?", (space_id,))
    db.x("UPDATE digests SET active=1 WHERE id=?", (digest_id,))
    db.log_event(space_id, None, "digest_rollback", {"digest_id": digest_id})


# ────────────────────────── 认知摘要蒸馏 ──────────────────────────

def distill_cognitive(space_id, progress=None):
    """整合候选+问答修正+库摘要 → 更新『部门认知』画像（人工审核在候选层把关）。

    progress: 可选回调 fn(stage, detail)，阶段事件写入 events 供界面显示进度。
    """
    def p(stage, detail):
        if progress:
            try:
                progress(stage=stage, detail=detail)
            except Exception:
                pass
    p("gather", "收集蒸馏材料…")
    old = db.one("SELECT * FROM profiles WHERE space_id=? AND name='cognitive'",
                 (space_id,))
    rules = db.q("SELECT statement FROM rules WHERE space_id=? "
                 "AND status='active'", (space_id,))
    libs = db.q("SELECT name, summary FROM libraries WHERE space_id=?",
                (space_id,))
    fixes = db.q("SELECT question, correction FROM qa_events WHERE space_id=?"
                 " AND feedback='fix' AND correction != '' "
                 "ORDER BY created_at DESC LIMIT 40", (space_id,))
    accepted_caps = db.q(
                "SELECT statement, evidence FROM candidates WHERE space_id=?"
                " AND status='approved' AND kind IN ('rule','capability')",
                (space_id,))
    p("gather", f"材料就绪：库 {len(libs)} · 候选 {len(accepted_caps)} · "
                f"规则 {len(rules)} · 纠正 {len(fixes)}")
    materials = {
        "库摘要": [{"库": l["name"], "摘要": l["summary"]} for l in libs],
        "已晋升候选（含出处）": accepted_caps[:40],
        "部门规则": [r["statement"] for r in rules],
        "用户修正": fixes[:20],
    }
    p("model", "模型整理认知（术语/能力/习惯）…")
    obj, _ = llm.complete_json(
        [{"role": "system", "content": COGNITIVE_PROMPT},
         {"role": "user", "content":
          "既有认知：" + (old["content"] if old else "（空）") +
          "\n\n新材料：" + json.dumps(materials, ensure_ascii=False)[:16000]}],
        max_tokens=2500)
    content = json.dumps({"glossary": obj.get("glossary", []),
                          "capabilities": obj.get("capabilities", []),
                          "habits": obj.get("habits", [])},
                         ensure_ascii=False)
    if old:
        db.x("UPDATE profiles SET content=?, updated_at=? WHERE id=?",
             (content, db.now(), old["id"]))
    else:
        db.x("INSERT INTO profiles(id,space_id,name,content,ttl_days,updated_at)"
             " VALUES(?,?, 'cognitive', ?, 90, ?)",
             (db.new_id("pf-"), space_id, content, db.now()))
    p("render", "重渲染知识发行版 digest…")
    return render(space_id)


# ────────────────────────── 候选审核 ──────────────────────────

def approve_candidate(candidate_id):
    row = db.one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
    if not row or row["status"] != "pending":
        return None
    db.x("UPDATE candidates SET status='approved', updated_at=? WHERE id=?",
         (db.now(), candidate_id))
    if row["kind"] in ("rule", "capability"):
        rid = db.x("INSERT INTO rules(space_id,statement,ex_bad,ex_good,status,"
                   "source,created_at) VALUES(?,?,?,?,'active',?,?)",
                   (row["space_id"], row["statement"], row["ex_bad"],
                    row["ex_good"], "candidate:" + candidate_id, db.now()))
        return {"rule_id": rid}
    return {"promoted": row["kind"]}


def reject_candidate(candidate_id):
    db.x("UPDATE candidates SET status='rejected', updated_at=? WHERE id=?",
         (db.now(), candidate_id))


def edit_approve(candidate_id, statement, ex_bad="", ex_good=""):
    db.x("UPDATE candidates SET statement=?, ex_bad=?, ex_good=? WHERE id=?",
         (statement[:600], ex_bad[:300], ex_good[:300], candidate_id))
    return approve_candidate(candidate_id)


def record_feedback(event_id, kind, correction=""):
    """问答反馈：ok 记录采纳；fix 记录修正并自动生成候选。"""
    ev = db.one("SELECT * FROM qa_events WHERE id=?", (event_id,))
    if not ev:
        return None
    db.x("UPDATE qa_events SET feedback=?, correction=? WHERE id=?",
         (kind, correction[:1000], event_id))
    if kind == "fix" and correction.strip():
        cid = add_candidate_from_fix(ev, correction)
        return {"candidate_id": cid}
    return {}


def add_candidate_from_fix(ev, correction):
    """用户修正 → 候选（陈述由 LLM 从修正语提炼，失败则直接用原文）。"""
    from . import ingest
    statement = correction.strip()
    try:
        obj, _ = llm.complete_json(
            [{"role": "system", "content":
              "把用户的纠正提炼为一句可长期使用的部门事实陈述（保留关键限定，"
              '注明系统名）。只输出 JSON：{"statement":"..."}'},
             {"role": "user", "content":
              f"原问题：{ev['question']}\n原回答结论：{ev['verdict']}\n"
              f"用户纠正：{correction[:500]}"}], max_tokens=200)
        statement = obj.get("statement") or statement
    except Exception:
        pass
    return ingest.add_candidate(
        ev["space_id"], "capability", statement,
        evidence=[{"qa_event": ev["id"], "question": ev["question"][:200]}],
        source="qa_fix:" + ev["id"])
