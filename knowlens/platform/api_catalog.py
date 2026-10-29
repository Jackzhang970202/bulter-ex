# -*- coding: utf-8 -*-
"""Agents, skills, experts and teams API."""
import json

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

import os

from .. import config, db
from . import auth, designer, llm_client, mcp_registry, runtime, services, skills

router = APIRouter(prefix="/api/platform", tags=["platform-catalog"])


def sse(gen):
    def wrapper():
        for event in gen:
            yield "data: " + json.dumps(event, ensure_ascii=False,
                                        default=str) + "\n\n"
    return StreamingResponse(wrapper(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _agent_row(agent_id):
    row = db.one("SELECT * FROM pf_agents WHERE id=?", (agent_id,))
    if not row:
        raise HTTPException(404, "智能体不存在")
    return row


def _agent_out(row):
    skill_rows = [db.one("SELECT id,name,kind,enabled FROM pf_skills WHERE id=?",
                         (sid,)) for sid in services.as_list(row["skill_ids"])]
    tool_rows = [{"id": t, "name": t} for t in services.as_list(row["tool_ids"])]
    data = services.agent_public(row, [s for s in skill_rows if s], tool_rows)
    data["skill_names"] = [s["name"] for s in skill_rows if s]
    data["stats"] = _agent_stats(row["id"])
    return data


def _agent_stats(agent_id):
    row = db.one(
        "SELECT COUNT(*) runs, COALESCE(SUM(tokens_in),0) tin, "
        "COALESCE(SUM(tokens_out),0) tout, "
        "COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),0) ok "
        "FROM pf_runs WHERE kind='agent' AND ref_id=?", (agent_id,))
    runs = row["runs"] or 0
    return {"runs": runs, "tokens_in": row["tin"], "tokens_out": row["tout"],
            "success_rate": round((row["ok"] or 0) / runs, 3) if runs else 0}


# ────────────────────────── agents ──────────────────────────

@router.get("/agents")
def list_agents(request: Request, status: str = "", q: str = "",
                category: str = "", limit: int = 60, offset: int = 0):
    auth.current_user(request)
    rows = db.q("SELECT * FROM pf_agents ORDER BY updated_at DESC")
    if status:
        rows = [r for r in rows if r["status"] == status]
    if category:
        rows = [r for r in rows if r["category"] == category]
    if q:
        rows = [r for r in rows if services.score_text(
            q, r["name"], r["title"], r["description"], r["tags"]) > 0]
    items = [_agent_out(r) for r in services.page(rows, limit, offset)["items"]]
    return {"items": items, "total": len(rows), "limit": max(1, min(limit, 200)),
            "offset": max(0, offset)}



AGENT_TEMPLATES = [
    {"id": "tpl_kb", "name": "知识库助手", "title": "基于资料回答",
     "description": "先检索知识空间，再给出结论、依据与页码；找不到依据时明确说明。",
     "avatar": "📚", "category": "knowledge",
     "system_prompt": "你是知识库助手。回答前先检索绑定空间，给出结论与依据；"
                      "引用格式为（文档名 第N页）；没有依据时说明未能定位。",
     "tool_ids": ["knowledge_search"], "tags": ["知识", "问答"]},
    {"id": "tpl_analyst", "name": "数据分析师", "title": "指标与口径",
     "description": "解释指标定义与计算过程，必要时用计算器复核数字。",
     "avatar": "📊", "category": "analysis",
     "system_prompt": "你是数据分析师。先确认指标口径，再给出计算过程与结论；"
                      "所有数字必须可复核，不确定时说明假设。",
     "tool_ids": ["calculator", "json_query", "current_time"],
     "tags": ["指标", "分析"]},
    {"id": "tpl_writer", "name": "文档审校员", "title": "写作与审校",
     "description": "检查结构、术语一致性与事实引用，输出修改前后对照。",
     "avatar": "📝", "category": "writing",
     "system_prompt": "你是文档审校员。逐条指出问题并给出修改前后对照，"
                      "不要重写全文；事实性内容要求提供出处。",
     "tool_ids": ["knowledge_search"], "tags": ["审校", "写作"]},
    {"id": "tpl_researcher", "name": "调研助手", "title": "检索与综合",
     "description": "先用知识库与网页搜索收集材料，再按要点综合并标注来源。",
     "avatar": "🔎", "category": "research",
     "system_prompt": "你是调研助手。先列出需要核实的要点，再检索内部知识与公开资料，"
                      "最后按要点综合，区分内部依据与公开来源。",
     "tool_ids": ["knowledge_search", "web_search"], "tags": ["调研", "综合"]},
]


@router.get("/agents/templates")
def agent_templates(request: Request):
    auth.current_user(request)
    return {"items": AGENT_TEMPLATES, "total": len(AGENT_TEMPLATES)}


@router.post("/agents")
def create_agent(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "智能体名称不能为空")
    aid = services.uid("ag_")
    now = services.now()
    db.x("INSERT INTO pf_agents(id,name,title,description,avatar,category,"
         "system_prompt,model,temperature,max_steps,space_id,library_ids,"
         "kb_ids,skill_ids,tool_ids,memory_enabled,status,version,owner_id,tags,"
         "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (aid, name, payload.get("title") or name,
          payload.get("description") or "", payload.get("avatar") or "🤖",
          payload.get("category") or "general",
          payload.get("system_prompt") or "", payload.get("model") or "",
          float(payload["temperature"])
          if payload.get("temperature") is not None else 0.2,
          int(payload.get("max_steps") or 6), payload.get("space_id") or "",
          services.jdump(services.as_list(payload.get("library_ids"))),
          services.jdump(services.as_list(payload.get("kb_ids"))),
          services.jdump(services.as_list(payload.get("skill_ids"))),
          services.jdump(services.as_list(payload.get("tool_ids"))),
          1 if payload.get("memory_enabled", True) else 0, "draft", 0,
          user["id"], services.jdump(services.parse_tags(payload.get("tags"))),
          now, now))
    services.audit(user["id"], "agent.create", aid, name)
    return _agent_out(_agent_row(aid))


@router.get("/agents/{agent_id}")
def get_agent(request: Request, agent_id: str):
    auth.current_user(request)
    return _agent_out(_agent_row(agent_id))


@router.patch("/agents/{agent_id}")
def update_agent(request: Request, agent_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = _agent_row(agent_id)
    fields = {
        "name": str, "title": str, "description": str, "avatar": str,
        "category": str, "system_prompt": str, "model": str,
        "temperature": float, "max_steps": int, "space_id": str,
        "library_ids": None, "kb_ids": None, "skill_ids": None,
        "tool_ids": None, "memory_enabled": None, "tags": None,
    }
    sets, args = [], []
    for key, caster in fields.items():
        if key not in payload:
            continue
        value = payload[key]
        if key in ("library_ids", "kb_ids", "skill_ids", "tool_ids", "tags"):
            value = services.jdump(services.as_list(value)
                                   if key != "tags" else services.parse_tags(value))
        elif key == "memory_enabled":
            value = 1 if value else 0
        elif caster:
            value = caster(value)
        sets.append(f"{key}=?")
        args.append(value)
    if not sets:
        return _agent_out(row)
    args.extend([services.now(), agent_id])
    db.x(f"UPDATE pf_agents SET {', '.join(sets)},updated_at=? WHERE id=?", args)
    services.audit(user["id"], "agent.update", agent_id, ",".join(
        k for k in payload if k in fields))
    return _agent_out(_agent_row(agent_id))


@router.delete("/agents/{agent_id}")
def delete_agent(request: Request, agent_id: str):
    user = auth.require(request, "admin")
    row = _agent_row(agent_id)
    for team in db.q("SELECT id,members,synthesizer_id FROM pf_teams"):
        members = [m for m in services.jload(team["members"], [])
                   if isinstance(m, dict) and m.get("agent_id") != agent_id]
        synth = "" if team.get("synthesizer_id") == agent_id \
            else team.get("synthesizer_id") or ""
        db.x("UPDATE pf_teams SET members=?,synthesizer_id=?,updated_at=? "
             "WHERE id=?", (services.jdump(members), synth,
                            services.now(), team["id"]))
    db.x("DELETE FROM pf_agent_versions WHERE agent_id=?", (agent_id,))
    db.x("DELETE FROM pf_agents WHERE id=?", (agent_id,))
    services.audit(user["id"], "agent.delete", agent_id, row["name"])
    return {"ok": True}


@router.post("/agents/{agent_id}/transition")
def agent_transition(request: Request, agent_id: str,
                     payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = _agent_row(agent_id)
    new_status, error = services.transition(
        row["status"], payload.get("action"), user["role"])
    if error:
        raise HTTPException(400, error)
    version = row["version"] or 0
    if new_status == "published":
        version += 1
        snapshot = {k: row[k] for k in row}
        db.x("INSERT INTO pf_agent_versions(id,agent_id,version,snapshot,"
             "created_at) VALUES(?,?,?,?,?)",
             (services.uid("av_"), agent_id, version,
              services.jdump(snapshot), services.now()))
    db.x("UPDATE pf_agents SET status=?,version=?,updated_at=? WHERE id=?",
         (new_status, version, services.now(), agent_id))
    services.audit(user["id"], f"agent.{payload.get('action')}", agent_id,
                   f"{row['status']} -> {new_status}")
    return _agent_out(_agent_row(agent_id))


@router.post("/agents/{agent_id}/clone")
def clone_agent(request: Request, agent_id: str):
    user = auth.require(request, "editor")
    row = _agent_row(agent_id)
    aid = services.uid("ag_")
    now = services.now()
    db.x("INSERT INTO pf_agents(id,name,title,description,avatar,category,"
         "system_prompt,model,temperature,max_steps,space_id,library_ids,"
         "skill_ids,tool_ids,memory_enabled,status,version,owner_id,tags,"
         "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (aid, row["name"] + " 副本", row["title"], row["description"],
          row["avatar"], row["category"], row["system_prompt"], row["model"],
          row["temperature"], row["max_steps"], row["space_id"],
          row["library_ids"], row["skill_ids"], row["tool_ids"],
          row["memory_enabled"], "draft", 0, user["id"], row["tags"],
          now, now))
    services.audit(user["id"], "agent.clone", aid, row["name"])
    return _agent_out(_agent_row(aid))


@router.get("/agents/{agent_id}/versions")
def agent_versions(request: Request, agent_id: str):
    auth.current_user(request)
    rows = db.q("SELECT id,agent_id,version,created_at FROM pf_agent_versions "
                "WHERE agent_id=? ORDER BY version DESC", (agent_id,))
    return {"items": rows, "total": len(rows)}


@router.get("/agents/{agent_id}/versions/{version}")
def agent_version(request: Request, agent_id: str, version: int):
    auth.current_user(request)
    row = db.one("SELECT * FROM pf_agent_versions WHERE agent_id=? AND version=?",
                 (agent_id, version))
    if not row:
        raise HTTPException(404, "版本不存在")
    return {"version": row["version"], "created_at": row["created_at"],
            "snapshot": services.jload(row["snapshot"], {})}


@router.post("/agents/{agent_id}/rollback")
def rollback_agent(request: Request, agent_id: str,
                   payload: dict = Body(default={})):
    user = auth.require(request, "admin")
    version = int(payload.get("version") or 0)
    row = db.one("SELECT * FROM pf_agent_versions WHERE agent_id=? AND version=?",
                 (agent_id, version))
    if not row:
        raise HTTPException(404, "版本不存在")
    snap = services.jload(row["snapshot"], {})
    keys = ("name", "title", "description", "avatar", "category",
            "system_prompt", "model", "temperature", "max_steps", "space_id",
            "library_ids", "skill_ids", "tool_ids", "memory_enabled", "tags")
    sets = ", ".join(f"{k}=?" for k in keys)
    db.x(f"UPDATE pf_agents SET {sets},version=?,status='published',"
         "updated_at=? WHERE id=?",
         tuple(snap.get(k) for k in keys) + (version, services.now(), agent_id))
    services.audit(user["id"], "agent.rollback", agent_id, str(version))
    return _agent_out(_agent_row(agent_id))


@router.get("/agents/{agent_id}/stats")
def agent_stats(request: Request, agent_id: str):
    auth.current_user(request)
    _agent_row(agent_id)
    runs = db.q("SELECT status,tokens_in,tokens_out,created_at FROM pf_runs "
                "WHERE kind='agent' AND ref_id=? ORDER BY created_at DESC "
                "LIMIT 100", (agent_id,))
    by_day = {}
    for r in runs:
        day = (r["created_at"] or "")[:10]
        by_day[day] = by_day.get(day, 0) + 1
    return {**_agent_stats(agent_id),
            "trend": [{"day": k, "runs": v}
                      for k, v in sorted(by_day.items())][-14:],
            "recent": runs[:20]}


@router.post("/agents/{agent_id}/run")
def run_agent(request: Request, agent_id: str, payload: dict = Body(default={})):
    user = auth.current_user(request)
    row = _agent_row(agent_id)
    prompt = (payload.get("message") or payload.get("input") or "").strip()
    if not prompt:
        raise HTTPException(400, "消息不能为空")
    return sse(runtime.stream_agent(
        row, prompt, user["id"], payload.get("session_id") or "",
        file_ids=payload.get("file_ids") or [],
        extra_skills=payload.get("skill_ids") or [],
        extra_tools=payload.get("tool_ids") or [],
        kb_ids=payload.get("kb_ids") or []))


@router.post("/agents/{agent_id}/test")
def test_agent(request: Request, agent_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = _agent_row(agent_id)
    prompt = (payload.get("message") or "").strip()
    if not prompt:
        raise HTTPException(400, "消息不能为空")
    result = runtime.agent_answer(row, prompt, user["id"],
                                  file_ids=payload.get("file_ids") or [])
    services.audit(user["id"], "agent.test", agent_id, prompt[:80])
    return result


@router.get("/agents/{agent_id}/runs")
def agent_runs(request: Request, agent_id: str, limit: int = 30):
    auth.current_user(request)
    rows = db.q("SELECT id,status,title,tokens_in,tokens_out,ms,created_at "
                "FROM pf_runs WHERE kind='agent' AND ref_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_id, max(1, min(limit, 200))))
    return {"items": rows, "total": len(rows)}


# ────────────────────────── evaluations ──────────────────────────

@router.get("/agents/{agent_id}/evals")
def list_evals(request: Request, agent_id: str):
    auth.current_user(request)
    rows = db.q("SELECT * FROM pf_evals WHERE agent_id=? ORDER BY created_at "
                "DESC", (agent_id,))
    for row in rows:
        row["cases"] = services.jload(row["cases"], [])
    return {"items": rows, "total": len(rows)}


@router.post("/agents/{agent_id}/evals")
def create_eval(request: Request, agent_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    _agent_row(agent_id)
    cases = payload.get("cases") or []
    if not isinstance(cases, list) or not cases:
        raise HTTPException(400, "至少需要一条测试用例")
    eid = services.uid("ev_")
    db.x("INSERT INTO pf_evals(id,agent_id,name,cases,created_at) "
         "VALUES(?,?,?,?,?)",
         (eid, agent_id, payload.get("name") or "默认评测集",
          services.jdump(cases[:50]), services.now()))
    services.audit(user["id"], "eval.create", eid, agent_id)
    return db.one("SELECT * FROM pf_evals WHERE id=?", (eid,))


@router.post("/agents/{agent_id}/evals/{eval_id}/run")
def run_eval(request: Request, agent_id: str, eval_id: str):
    user = auth.require(request, "editor")
    agent = _agent_row(agent_id)
    row = db.one("SELECT * FROM pf_evals WHERE id=? AND agent_id=?",
                 (eval_id, agent_id))
    if not row:
        raise HTTPException(404, "评测集不存在")
    cases = services.jload(row["cases"], [])
    detail, hits = [], 0
    for case in cases:
        question = case.get("q") or case.get("question") or ""
        expect = case.get("expect") or case.get("keywords") or []
        if isinstance(expect, str):
            expect = [x.strip() for x in expect.split(",") if x.strip()]
        result = runtime.agent_answer(agent, question, user["id"])
        text = (result.get("text") or "").lower()
        matched = [k for k in expect if str(k).lower() in text]
        ok = bool(matched) if expect else bool(text)
        hits += 1 if ok else 0
        detail.append({"question": question, "ok": ok,
                       "matched": matched, "answer": text[:600]})
    score = round(hits / len(cases), 3) if cases else 0
    rid = services.uid("er_")
    db.x("INSERT INTO pf_eval_runs(id,eval_id,agent_id,version,score,detail,"
         "created_at) VALUES(?,?,?,?,?,?,?)",
         (rid, eval_id, agent_id, agent["version"] or 0, score,
          services.jdump(detail), services.now()))
    services.audit(user["id"], "eval.run", rid, f"score={score}")
    return {"id": rid, "score": score, "total": len(cases), "detail": detail}


@router.get("/agents/{agent_id}/evals/{eval_id}/runs")
def eval_runs(request: Request, agent_id: str, eval_id: str):
    auth.current_user(request)
    rows = db.q("SELECT id,version,score,created_at FROM pf_eval_runs "
                "WHERE eval_id=? ORDER BY created_at DESC", (eval_id,))
    return {"items": rows, "total": len(rows)}


# ────────────────────────── skills ──────────────────────────

@router.get("/skills/catalog")
def catalog(request: Request, q: str = "", category: str = "",
            kind: str = "", license: str = "", verified: str = "",
            limit: int = 60, offset: int = 0):
    auth.current_user(request)
    verified_flag = None
    if verified in ("1", "true"):
        verified_flag = True
    return skills.search(q, category, kind, license, verified_flag, limit,
                         offset)


@router.get("/skills/catalog/facets")
def catalog_facets(request: Request):
    auth.current_user(request)
    return {**skills.facets(), **skills.catalog_meta()}


@router.get("/skills/catalog/{catalog_id}")
def catalog_detail(request: Request, catalog_id: str):
    auth.current_user(request)
    row = skills.detail(catalog_id)
    if not row:
        raise HTTPException(404, "技能不存在")
    return row


@router.post("/skills/catalog/refresh")
def refresh_catalog(request: Request):
    user = auth.require(request, "editor")
    skills._cache["mtime"] = None
    rows = skills._load()
    services.audit(user["id"], "skill.catalog.refresh", "catalog", str(len(rows)))
    return {"ok": True, "total": len(rows), "generated_at": skills._generated_at}


@router.post("/skills/install")
def install_skill(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    catalog_id = payload.get("catalog_id") or ""
    row, error = skills.install(catalog_id, user["id"])
    if error:
        raise HTTPException(400, error)
    return services.skill_public(row)


@router.get("/skills")
def list_skills(request: Request, q: str = "", enabled: str = "",
                category: str = "", limit: int = 100, offset: int = 0):
    auth.current_user(request)
    rows = db.q("SELECT * FROM pf_skills ORDER BY created_at DESC")
    if enabled in ("0", "1"):
        rows = [r for r in rows if str(r["enabled"]) == enabled]
    if category:
        rows = [r for r in rows if r["category"] == category]
    if q:
        rows = [r for r in rows if services.score_text(
            q, r["name"], r["title"], r["description"], r["category"]) > 0]
    items = [services.skill_public(r)
             for r in services.page(rows, limit, offset)["items"]]
    return {"items": items, "total": len(rows), "limit": max(1, min(limit, 200)),
            "offset": max(0, offset)}


@router.post("/skills")
def create_skill(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row, error = skills.create_custom(payload, user["id"])
    if error:
        raise HTTPException(400, error)
    return services.skill_public(row)


@router.post("/skills/import")
def import_skill(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row, error = skills.import_url(payload.get("url") or "", user["id"])
    if error:
        raise HTTPException(400, error)
    return services.skill_public(row)


@router.patch("/skills/{skill_id}")
def update_skill(request: Request, skill_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = db.one("SELECT * FROM pf_skills WHERE id=?", (skill_id,))
    if not row:
        raise HTTPException(404, "技能不存在")
    sets, args = [], []
    for key in ("name", "title", "description", "category", "kind", "content"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(str(payload[key]))
    for key in ("config",):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(services.jdump(payload[key]))
    if "enabled" in payload:
        sets.append("enabled=?")
        args.append(1 if payload["enabled"] else 0)
    if sets:
        args.extend([services.now(), skill_id])
        db.x(f"UPDATE pf_skills SET {', '.join(sets)},updated_at=? WHERE id=?",
             args)
        services.audit(user["id"], "skill.update", skill_id, ",".join(payload))
    return services.skill_public(
        db.one("SELECT * FROM pf_skills WHERE id=?", (skill_id,)))


@router.delete("/skills/{skill_id}")
def delete_skill(request: Request, skill_id: str):
    user = auth.require(request, "editor")
    if not skills.uninstall(skill_id, user["id"]):
        raise HTTPException(404, "技能不存在")
    return {"ok": True}


@router.post("/skills/{skill_id}/enable")
@router.post("/skills/{skill_id}/disable")
def toggle_skill(request: Request, skill_id: str):
    user = auth.require(request, "editor")
    enabled = 1 if request.url.path.endswith("/enable") else 0
    db.x("UPDATE pf_skills SET enabled=?,updated_at=? WHERE id=?",
         (enabled, services.now(), skill_id))
    services.audit(user["id"], "skill.enable" if enabled else "skill.disable",
                   skill_id, "")
    row = db.one("SELECT * FROM pf_skills WHERE id=?", (skill_id,))
    if not row:
        raise HTTPException(404, "技能不存在")
    return services.skill_public(row)


@router.post("/skills/{skill_id}/run")
def run_skill(request: Request, skill_id: str, payload: dict = Body(default={})):
    user = auth.current_user(request)
    row = db.one("SELECT * FROM pf_skills WHERE id=?", (skill_id,))
    if not row:
        raise HTTPException(404, "技能不存在")
    if not row["enabled"]:
        raise HTTPException(400, "技能已停用")
    return sse(runtime.stream_skill(row, payload.get("input") or "",
                                    user["id"]))



@router.get("/skills/{skill_id}/file")
def skill_file(request: Request, skill_id: str, path: str):
    auth.current_user(request)
    row = db.one("SELECT * FROM pf_skills WHERE id=?", (skill_id,))
    if not row or not row.get("package_dir"):
        raise HTTPException(404, "该技能没有本地文件")
    root = os.path.abspath(row["package_dir"])
    target = os.path.abspath(os.path.join(root, path))
    if not target.startswith(root + os.sep) or not os.path.isfile(target):
        raise HTTPException(404, "文件不存在")
    if os.path.getsize(target) > 200_000:
        raise HTTPException(400, "文件过大，请下载后查看")
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read(100_000)
    except Exception as exc:
        raise HTTPException(400, f"无法读取：{exc}")
    return {"path": path, "bytes": os.path.getsize(target), "content": content}


@router.get("/tools")
def list_tools(request: Request):
    auth.current_user(request)
    from . import tools
    return {"items": tools.list_tools(), "total": len(tools.TOOLS)}




# ────────────────────────── MCP registry ──────────────────────────


@router.post("/design/chat")
def design_chat(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    kind = payload.get("kind") or "workflow"
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        raise HTTPException(400, "messages 必须是数组")
    result = designer.chat(kind, messages, payload.get("draft") or None)
    services.audit(user["id"], "design.chat", kind,
                   str((messages[-1] or {}).get("content") or "")[:120])
    return result


@router.post("/design/draft")
def design_draft(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    kind = payload.get("kind") or "workflow"
    if kind not in ("workflow", "agent", "team"):
        raise HTTPException(400, "只支持 workflow / agent / team")
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "请描述你要完成的事")
    result = designer.draft(kind, prompt, payload.get("answers") or {},
                            payload.get("draft") or None)
    services.audit(user["id"], "design.draft", kind, prompt[:120])
    return result


@router.get("/mcp/servers")
def mcp_servers(request: Request, q: str = "", limit: int = 40,
                cursor: str = ""):
    auth.current_user(request)
    try:
        return mcp_registry.search(q, limit, cursor)
    except Exception as exc:
        raise HTTPException(502, f"MCP 注册表不可用: {exc}")


@router.get("/mcp/tools")
def mcp_tools(request: Request, url: str):
    auth.current_user(request)
    result = skills.mcp_list_tools(url)
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "无法读取工具列表")
    return result


@router.post("/mcp/install")
def mcp_install(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    url = (payload.get("url") or "").strip()
    tool = (payload.get("tool") or "").strip()
    if not url or not tool:
        raise HTTPException(400, "需要 MCP 地址与工具名")
    name = (payload.get("name") or tool).strip()
    row, error = skills.create_custom({
        "name": name,
        "title": payload.get("title") or name,
        "description": payload.get("description") or "",
        "category": "mcp",
        "kind": "mcp",
        "content": "",
        "config": {"url": url, "tool": tool,
                   "arguments": payload.get("arguments") or {}},
        "source_url": payload.get("repository") or url,
    }, user["id"])
    if error:
        raise HTTPException(400, error)
    services.audit(user["id"], "mcp.install", row["id"], f"{url}#{tool}")
    return services.skill_public(row)


# ────────────────────────── skill packs ──────────────────────────

def _pack_out(row):
    data = dict(row)
    data["skill_ids"] = services.as_list(row.get("skill_ids"))
    data["skills"] = [r for r in (
        db.one("SELECT id,name,kind,enabled FROM pf_skills WHERE id=?", (sid,))
        for sid in data["skill_ids"]) if r]
    return data


@router.get("/skill-packs")
def list_packs(request: Request):
    auth.current_user(request)
    rows = db.q("SELECT * FROM pf_skill_packs ORDER BY updated_at DESC")
    return {"items": [_pack_out(r) for r in rows], "total": len(rows)}


@router.post("/skill-packs")
def create_pack(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "技能包名称不能为空")
    pid = services.uid("pk_")
    now = services.now()
    db.x("INSERT INTO pf_skill_packs(id,name,description,skill_ids,owner_id,"
         "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
         (pid, name, payload.get("description") or "",
          services.jdump(services.as_list(payload.get("skill_ids"))),
          user["id"], now, now))
    services.audit(user["id"], "pack.create", pid, name)
    return _pack_out(db.one("SELECT * FROM pf_skill_packs WHERE id=?", (pid,)))


@router.patch("/skill-packs/{pack_id}")
def update_pack(request: Request, pack_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = db.one("SELECT * FROM pf_skill_packs WHERE id=?", (pack_id,))
    if not row:
        raise HTTPException(404, "技能包不存在")
    sets, args = [], []
    for key in ("name", "description"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(str(payload[key]))
    if "skill_ids" in payload:
        sets.append("skill_ids=?")
        args.append(services.jdump(services.as_list(payload["skill_ids"])))
    if sets:
        args.extend([services.now(), pack_id])
        db.x(f"UPDATE pf_skill_packs SET {', '.join(sets)},updated_at=? "
             "WHERE id=?", args)
        services.audit(user["id"], "pack.update", pack_id, ",".join(payload))
    return _pack_out(db.one("SELECT * FROM pf_skill_packs WHERE id=?", (pack_id,)))


@router.delete("/skill-packs/{pack_id}")
def delete_pack(request: Request, pack_id: str):
    user = auth.require(request, "editor")
    db.x("DELETE FROM pf_skill_packs WHERE id=?", (pack_id,))
    services.audit(user["id"], "pack.delete", pack_id, "")
    return {"ok": True}


@router.post("/skill-packs/{pack_id}/apply")
def apply_pack(request: Request, pack_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    pack = db.one("SELECT * FROM pf_skill_packs WHERE id=?", (pack_id,))
    if not pack:
        raise HTTPException(404, "技能包不存在")
    agent = _agent_row(payload.get("agent_id") or "")
    ids = services.as_list(agent["skill_ids"])
    for sid in services.as_list(pack["skill_ids"]):
        if sid not in ids:
            ids.append(sid)
    db.x("UPDATE pf_agents SET skill_ids=?,updated_at=? WHERE id=?",
         (services.jdump(ids), services.now(), agent["id"]))
    services.audit(user["id"], "pack.apply", pack_id, agent["id"])
    return {"ok": True, "agent_id": agent["id"], "skill_ids": ids}


# ────────────────────────── experts ──────────────────────────

@router.get("/experts")
def list_experts(request: Request, q: str = "", featured: str = "",
                 limit: int = 60, offset: int = 0):
    auth.current_user(request)
    rows = db.q("SELECT * FROM pf_experts ORDER BY featured DESC,uses DESC")
    if featured in ("0", "1"):
        rows = [r for r in rows if str(r["featured"]) == featured]
    if q:
        rows = [r for r in rows if services.score_text(
            q, r["name"], r["title"], r["bio"], r["tags"], r["expertise"]) > 0]
    items = [_expert_out(r)
             for r in services.page(rows, limit, offset)["items"]]
    return {"items": items, "total": len(rows), "limit": max(1, min(limit, 200)),
            "offset": max(0, offset)}


def _expert_out(row):
    data = dict(row)
    data["tags"] = services.as_list(row.get("tags"))
    data["expertise"] = services.as_list(row.get("expertise"))
    data["featured"] = bool(row.get("featured"))
    agent = db.one("SELECT id,name,status FROM pf_agents WHERE id=?",
                   (row.get("agent_id"),))
    data["agent"] = agent
    return data


@router.post("/experts")
def create_expert(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "专家名称不能为空")
    eid = services.uid("ex_")
    now = services.now()
    db.x("INSERT INTO pf_experts(id,agent_id,name,title,bio,expertise,tags,"
         "rating,uses,status,featured,owner_id,created_at,updated_at) "
         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (eid, payload.get("agent_id") or "", name,
          payload.get("title") or name, payload.get("bio") or "",
          services.jdump(services.parse_tags(payload.get("expertise"))),
          services.jdump(services.parse_tags(payload.get("tags"))),
          0, 0, payload.get("status") or "draft",
          1 if payload.get("featured") else 0, user["id"], now, now))
    services.audit(user["id"], "expert.create", eid, name)
    return _expert_out(db.one("SELECT * FROM pf_experts WHERE id=?", (eid,)))


@router.patch("/experts/{expert_id}")
def update_expert(request: Request, expert_id: str,
                  payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = db.one("SELECT * FROM pf_experts WHERE id=?", (expert_id,))
    if not row:
        raise HTTPException(404, "专家不存在")
    sets, args = [], []
    for key in ("agent_id", "name", "title", "bio", "status"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(str(payload[key]))
    for key in ("expertise", "tags"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(services.jdump(services.parse_tags(payload[key])))
    if "featured" in payload:
        sets.append("featured=?")
        args.append(1 if payload["featured"] else 0)
    if sets:
        args.extend([services.now(), expert_id])
        db.x(f"UPDATE pf_experts SET {', '.join(sets)},updated_at=? WHERE id=?",
             args)
        services.audit(user["id"], "expert.update", expert_id, ",".join(payload))
    return _expert_out(db.one("SELECT * FROM pf_experts WHERE id=?", (expert_id,)))


@router.delete("/experts/{expert_id}")
def delete_expert(request: Request, expert_id: str):
    user = auth.require(request, "admin")
    db.x("DELETE FROM pf_experts WHERE id=?", (expert_id,))
    services.audit(user["id"], "expert.delete", expert_id, "")
    return {"ok": True}


@router.post("/experts/{expert_id}/run")
def run_expert(request: Request, expert_id: str,
               payload: dict = Body(default={})):
    user = auth.current_user(request)
    expert = db.one("SELECT * FROM pf_experts WHERE id=?", (expert_id,))
    if not expert:
        raise HTTPException(404, "专家不存在")
    agent = db.one("SELECT * FROM pf_agents WHERE id=?", (expert["agent_id"],))
    if not agent:
        raise HTTPException(400, "专家未绑定智能体")
    prompt = (payload.get("message") or "").strip()
    if not prompt:
        raise HTTPException(400, "消息不能为空")
    db.x("UPDATE pf_experts SET uses=uses+1 WHERE id=?", (expert_id,))
    return sse(runtime.stream_agent(
        agent, prompt, user["id"], payload.get("session_id") or "",
        by_expert=expert["name"],
        file_ids=payload.get("file_ids") or [],
        extra_skills=payload.get("skill_ids") or [],
        extra_tools=payload.get("tool_ids") or [],
        kb_ids=payload.get("kb_ids") or []))


@router.get("/experts/{expert_id}/stats")
def expert_stats(request: Request, expert_id: str):
    auth.current_user(request)
    expert = db.one("SELECT * FROM pf_experts WHERE id=?", (expert_id,))
    if not expert:
        raise HTTPException(404, "专家不存在")
    agent_id = expert.get("agent_id") or ""
    row = db.one(
        "SELECT COUNT(*) runs, COALESCE(SUM(tokens_in),0) tin, "
        "COALESCE(SUM(tokens_out),0) tout, "
        "COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),0) ok "
        "FROM pf_runs WHERE kind='agent' AND ref_id=?", (agent_id,))
    runs = row["runs"] or 0
    return {"uses": expert["uses"], "rating": expert["rating"],
            "agent_runs": runs, "tokens": (row["tin"] or 0) + (row["tout"] or 0),
            "success_rate": round((row["ok"] or 0) / runs, 3) if runs else 0,
            "agent_id": agent_id}


@router.post("/experts/{expert_id}/rate")
def rate_expert(request: Request, expert_id: str,
                payload: dict = Body(default={})):
    auth.current_user(request)
    score = max(1, min(int(payload.get("score") or 5), 5))
    row = db.one("SELECT rating,uses FROM pf_experts WHERE id=?", (expert_id,))
    if not row:
        raise HTTPException(404, "专家不存在")
    uses = row["uses"] or 0
    previous = max(0, uses - 1)  # the current consultation was counted before rating
    rating = (((row["rating"] or 0) * previous) + score) / (previous + 1)
    db.x("UPDATE pf_experts SET rating=? WHERE id=?", (round(rating, 2), expert_id))
    return {"ok": True, "rating": round(rating, 2)}


# ────────────────────────── teams ──────────────────────────

def _normalise_members(value):
    if not isinstance(value, list):
        raise HTTPException(400, "members 必须是数组")
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise HTTPException(400, "每个成员必须是对象")
        agent_id = str(item.get("agent_id") or "").strip()
        if not agent_id:
            raise HTTPException(400, "成员缺少 agent_id")
        out.append({"agent_id": agent_id,
                    "role": str(item.get("role") or "")[:40]})
    return out


def _team_out(row):
    data = dict(row)
    data["kb_ids"] = services.as_list(row.get("kb_ids"))
    members = []
    for member in services.jload(row.get("members"), []):
        if not isinstance(member, dict):
            continue
        agent = db.one("SELECT id,name,avatar,status FROM pf_agents WHERE id=?",
                       (member.get("agent_id"),))
        member = dict(member)
        member["agent"] = agent
        members.append(member)
    data["members"] = members
    return data



TEAM_TEMPLATES = [
    {"id": "team_review", "name": "双人评审组", "description":
     "一人起草、一人挑错，最后由综合智能体给出定稿。",
     "strategy": "roundtable", "max_turns": 2, "members": []},
    {"id": "team_research", "name": "调研小组", "description":
     "主管按问题逐轮指派成员，最后汇总；适合信息分散的任务。",
     "strategy": "supervisor", "max_turns": 6, "members": []},
    {"id": "team_handoff", "name": "流水线交接", "description":
     "按成员顺序依次处理，上一位的输出作为下一位的输入。",
     "strategy": "handoff", "max_turns": 4, "members": []},
]


@router.get("/teams/templates")
def team_templates(request: Request):
    auth.current_user(request)
    return {"items": TEAM_TEMPLATES, "total": len(TEAM_TEMPLATES)}


@router.get("/teams")
def list_teams(request: Request):
    auth.current_user(request)
    rows = db.q("SELECT * FROM pf_teams ORDER BY updated_at DESC")
    return {"items": [_team_out(r) for r in rows], "total": len(rows)}


@router.post("/teams")
def create_team(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "团队名称不能为空")
    tid = services.uid("tm_")
    now = services.now()
    db.x("INSERT INTO pf_teams(id,name,description,strategy,members,"
         "synthesizer_id,max_turns,kb_ids,status,owner_id,created_at,"
         "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
         (tid, name, payload.get("description") or "",
          payload.get("strategy") or "roundtable",
          services.jdump(_normalise_members(payload.get("members") or [])),
          payload.get("synthesizer_id") or "",
          int(payload.get("max_turns") or 4),
          services.jdump(services.as_list(payload.get("kb_ids"))),
          "draft", user["id"], now, now))
    services.audit(user["id"], "team.create", tid, name)
    return _team_out(db.one("SELECT * FROM pf_teams WHERE id=?", (tid,)))


@router.patch("/teams/{team_id}")
def update_team(request: Request, team_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = db.one("SELECT * FROM pf_teams WHERE id=?", (team_id,))
    if not row:
        raise HTTPException(404, "团队不存在")
    sets, args = [], []
    for key in ("name", "description", "strategy", "synthesizer_id"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(str(payload[key]))
    if "members" in payload:
        sets.append("members=?")
        args.append(services.jdump(_normalise_members(payload["members"])))
    if "kb_ids" in payload:
        sets.append("kb_ids=?")
        args.append(services.jdump(services.as_list(payload["kb_ids"])))
    if "max_turns" in payload:
        sets.append("max_turns=?")
        args.append(int(payload["max_turns"]))
    if "status" in payload:
        sets.append("status=?")
        args.append(str(payload["status"]))
    if sets:
        args.extend([services.now(), team_id])
        db.x(f"UPDATE pf_teams SET {', '.join(sets)},updated_at=? WHERE id=?",
             args)
        services.audit(user["id"], "team.update", team_id, ",".join(payload))
    return _team_out(db.one("SELECT * FROM pf_teams WHERE id=?", (team_id,)))


@router.delete("/teams/{team_id}")
def delete_team(request: Request, team_id: str):
    user = auth.require(request, "admin")
    db.x("DELETE FROM pf_teams WHERE id=?", (team_id,))
    services.audit(user["id"], "team.delete", team_id, "")
    return {"ok": True}


@router.post("/teams/{team_id}/run")
def run_team(request: Request, team_id: str, payload: dict = Body(default={})):
    user = auth.current_user(request)
    team = db.one("SELECT * FROM pf_teams WHERE id=?", (team_id,))
    if not team:
        raise HTTPException(404, "团队不存在")
    prompt = (payload.get("message") or "").strip()
    if not prompt:
        raise HTTPException(400, "消息不能为空")
    return sse(runtime.stream_team(
        team, prompt, user["id"], payload.get("session_id") or "",
        file_ids=payload.get("file_ids") or [],
        extra_skills=payload.get("skill_ids") or [],
        extra_tools=payload.get("tool_ids") or [],
        kb_ids=payload.get("kb_ids") or []))