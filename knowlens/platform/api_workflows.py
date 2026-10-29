# -*- coding: utf-8 -*-
"""Workflow, run, memory and schedule API."""
from fastapi import APIRouter, Body, HTTPException, Request

from .. import db
from . import (api_catalog, auth, memory, scheduler, services, workflows)

router = APIRouter(prefix="/api/platform", tags=["platform-workflows"])


def _workflow_out(row):
    data = dict(row)
    data["nodes"] = services.jload(row.get("nodes"), [])
    data["input_schema"] = services.jload(row.get("input_schema"), [])
    data["kb_ids"] = services.as_list(row.get("kb_ids"))
    return data


TEMPLATES = [
    {
        "id": "tpl_knowledge_brief",
        "name": "知识简报",
        "description": "检索知识库 → 生成结构化简报 → 输出",
        "input_schema": [
            {"key": "input", "label": "主题", "type": "textarea",
             "required": True, "placeholder": "这次简报要覆盖什么？"},
            {"key": "length", "label": "输出长度", "type": "radio",
             "options": [{"value": "short", "label": "简短"},
                         {"value": "std", "label": "标准"},
                         {"value": "long", "label": "详细"}]},
            {"key": "files", "label": "附件", "type": "file_list",
             "max": 5},
        ],
        "nodes": [
            {"id": "start", "type": "start", "label": "开始", "next": "search"},
            {"id": "search", "type": "knowledge", "label": "知识检索",
             "config": {"top_k": 6, "space_id": ""}, "next": "write"},
            {"id": "write", "type": "llm", "label": "撰写简报",
             "config": {"prompt": "根据以下资料写一份不超过 600 字的知识简报，"
                                  "列出关键结论并标注来源：\n{prev}"}, "next": "approve"},
            {"id": "approve", "type": "approval", "label": "人工确认",
             "config": {"message": "简报已生成，是否采纳？"}, "next": "end"},
            {"id": "end", "type": "end", "label": "结束"},
        ],
    },
    {
        "id": "tpl_agent_review",
        "name": "双智能体评审",
        "description": "起草 → 评审 → 汇总，带人工确认",
        "input_schema": [
            {"key": "input", "label": "任务说明", "type": "textarea",
             "required": True},
            {"key": "tone", "label": "语气", "type": "select",
             "options": [{"value": "formal", "label": "正式"},
                         {"value": "plain", "label": "平实"}]},
            {"key": "files", "label": "附件", "type": "file_list",
             "max": 5},
        ],
        "nodes": [
            {"id": "start", "type": "start", "label": "开始", "next": "draft"},
            {"id": "draft", "type": "llm", "label": "起草",
             "config": {"prompt": "针对以下任务给出初稿：\n{input}"},
             "next": "review"},
            {"id": "review", "type": "llm", "label": "评审",
             "config": {"prompt": "请指出以下初稿的问题并给出修改建议：\n{prev}"},
             "next": "approve"},
            {"id": "approve", "type": "approval", "label": "人工确认",
             "config": {"message": "评审已完成，是否采纳并汇总？"},
             "next": "final"},
            {"id": "final", "type": "llm", "label": "汇总",
             "config": {"prompt": "整合初稿与评审意见，输出最终版本：\n{input}"},
             "next": "end"},
            {"id": "end", "type": "end", "label": "结束"},
        ],
    },
    {
        "id": "tpl_condition",
        "name": "条件分流",
        "description": "按输入长度走详细或简版处理",
        "input_schema": [
            {"key": "input", "label": "输入", "type": "textarea",
             "required": True},
        ],
        "nodes": [
            {"id": "start", "type": "start", "label": "开始", "next": "check"},
            {"id": "check", "type": "condition", "label": "判断",
             "config": {"expr": "len(input) > 80"},
             "branches": {"true": "long", "false": "short"}},
            {"id": "long", "type": "llm", "label": "详细处理",
             "config": {"prompt": "请详细分析：\n{input}"}, "next": "end"},
            {"id": "short", "type": "llm", "label": "简版处理",
             "config": {"prompt": "请用三句话回答：\n{input}"}, "next": "end"},
            {"id": "end", "type": "end", "label": "结束"},
        ],
    },
]


# ────────────────────────── workflows ──────────────────────────

@router.get("/workflows")
def list_workflows(request: Request):
    auth.current_user(request)
    rows = db.q("SELECT * FROM pf_workflows ORDER BY updated_at DESC")
    return {"items": [_workflow_out(r) for r in rows], "total": len(rows)}


@router.get("/workflows/templates")
def workflow_templates(request: Request):
    auth.current_user(request)
    return {"items": TEMPLATES, "total": len(TEMPLATES)}


@router.post("/workflows/validate")
def validate_workflow(request: Request, payload: dict = Body(default={})):
    auth.current_user(request)
    errors = workflows.validate(payload.get("nodes") or [])
    return {"ok": not errors, "errors": errors}


@router.post("/workflows")
def create_workflow(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "工作流名称不能为空")
    nodes = payload.get("nodes") or []
    errors = workflows.validate(nodes)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    wid = services.uid("wf_")
    now = services.now()
    db.x("INSERT INTO pf_workflows(id,name,description,nodes,kb_ids,status,"
         "version,owner_id,created_at,updated_at) "
         "VALUES(?,?,?,?,?,?,?,?,?,?)",
         (wid, name, payload.get("description") or "", services.jdump(nodes),
          services.jdump(services.as_list(payload.get("kb_ids"))),
          "draft", 0, user["id"], now, now))
    schema = payload.get("input_schema") or []
    if schema:
        db.x("UPDATE pf_workflows SET input_schema=? WHERE id=?",
             (services.jdump(schema), wid))
    services.audit(user["id"], "workflow.create", wid, name)
    return _workflow_out(db.one("SELECT * FROM pf_workflows WHERE id=?", (wid,)))


@router.get("/workflows/{workflow_id}")
def get_workflow(request: Request, workflow_id: str):
    auth.current_user(request)
    row = db.one("SELECT * FROM pf_workflows WHERE id=?", (workflow_id,))
    if not row:
        raise HTTPException(404, "工作流不存在")
    return _workflow_out(row)


@router.patch("/workflows/{workflow_id}")
def update_workflow(request: Request, workflow_id: str,
                    payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = db.one("SELECT * FROM pf_workflows WHERE id=?", (workflow_id,))
    if not row:
        raise HTTPException(404, "工作流不存在")
    sets, args = [], []
    for key in ("name", "description", "status"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(str(payload[key]))
    if "nodes" in payload:
        errors = workflows.validate(payload["nodes"])
        if errors:
            raise HTTPException(400, "; ".join(errors))
        sets.append("nodes=?")
        args.append(services.jdump(payload["nodes"]))
    if "input_schema" in payload:
        sets.append("input_schema=?")
        args.append(services.jdump(payload["input_schema"] or []))
    if "kb_ids" in payload:
        sets.append("kb_ids=?")
        args.append(services.jdump(services.as_list(payload["kb_ids"])))
    if "version" in payload:
        sets.append("version=?")
        args.append(int(payload["version"]))
    if sets:
        args.extend([services.now(), workflow_id])
        db.x(f"UPDATE pf_workflows SET {', '.join(sets)},updated_at=? "
             "WHERE id=?", args)
        services.audit(user["id"], "workflow.update", workflow_id,
                       ",".join(payload))
    return _workflow_out(db.one("SELECT * FROM pf_workflows WHERE id=?",
                                (workflow_id,)))


@router.delete("/workflows/{workflow_id}")
def delete_workflow(request: Request, workflow_id: str):
    user = auth.require(request, "admin")
    db.x("DELETE FROM pf_workflows WHERE id=?", (workflow_id,))
    services.audit(user["id"], "workflow.delete", workflow_id, "")
    return {"ok": True}


@router.post("/workflows/{workflow_id}/run")
def run_workflow(request: Request, workflow_id: str,
                 payload: dict = Body(default={})):
    user = auth.current_user(request)
    row = db.one("SELECT * FROM pf_workflows WHERE id=?", (workflow_id,))
    if not row:
        raise HTTPException(404, "工作流不存在")
    text = (payload.get("input") or payload.get("message") or "").strip()
    inputs = payload.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise HTTPException(400, "inputs 必须是对象")
    if not text and not inputs:
        raise HTTPException(400, "输入不能为空")
    gen = workflows.stream_workflow(
        _workflow_out(row), text, user["id"], inputs=inputs,
        file_ids=payload.get("file_ids") or [],
        kb_ids=payload.get("kb_ids") or [])
    return api_catalog.sse(gen)


@router.get("/workflows/{workflow_id}/runs")
def workflow_runs(request: Request, workflow_id: str, limit: int = 30):
    auth.current_user(request)
    rows = db.q("SELECT id,status,title,ms,tokens_in,tokens_out,created_at "
                "FROM pf_runs WHERE kind='workflow' AND ref_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (workflow_id, max(1, min(limit, 200))))
    return {"items": rows, "total": len(rows)}


# ────────────────────────── runs ──────────────────────────

@router.get("/runs")
def list_runs(request: Request, kind: str = "", ref_id: str = "",
              status: str = "", limit: int = 50, offset: int = 0):
    auth.current_user(request)
    sql, args = "SELECT * FROM pf_runs WHERE 1=1", []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if ref_id:
        sql += " AND ref_id=?"
        args.append(ref_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY created_at DESC"
    rows = db.q(sql, tuple(args))
    return services.page(rows, limit, offset)


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: str):
    auth.current_user(request)
    run = db.one("SELECT * FROM pf_runs WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404, "运行不存在")
    run["input"] = services.jload(run.get("input"), {})
    steps = db.q("SELECT * FROM pf_run_steps WHERE run_id=? ORDER BY seq",
                 (run_id,))
    for step in steps:
        step["input"] = services.jload(step.get("input"), {})
    artifacts = db.q("SELECT * FROM pf_artifacts WHERE run_id=?", (run_id,))
    return {"run": run, "steps": steps, "artifacts": artifacts}


@router.get("/runs/{run_id}/steps")
def run_steps(request: Request, run_id: str):
    auth.current_user(request)
    steps = db.q("SELECT * FROM pf_run_steps WHERE run_id=? ORDER BY seq",
                 (run_id,))
    for step in steps:
        step["input"] = services.jload(step.get("input"), {})
    return {"items": steps, "total": len(steps)}


@router.post("/runs/{run_id}/cancel")
def cancel_run(request: Request, run_id: str):
    user = auth.current_user(request)
    db.x("UPDATE pf_runs SET cancel_requested=1 WHERE id=?", (run_id,))
    services.audit(user["id"], "run.cancel", run_id, "")
    return {"ok": True}


@router.post("/runs/{run_id}/approve")
def approve_run(request: Request, run_id: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    approved = payload.get("approved", True)
    result, error = workflows.resume(run_id, bool(approved), user["id"])
    if error:
        raise HTTPException(400, error)
    run = db.one("SELECT * FROM pf_runs WHERE id=?", (run_id,))
    services.audit(user["id"], "run.approve", run_id, str(approved))
    if approved and result.get("next"):
        workflow = db.one("SELECT * FROM pf_workflows WHERE id=?",
                          (run["ref_id"],))
        gen = workflows.stream_workflow(
            _workflow_out(workflow), result.get("input") or "",
            user["id"], run_id=run_id, start_id=result["next"],
            initial_prev=result.get("prev"))
        return api_catalog.sse(gen)
    return {"ok": True, "status": result.get("status")}


# ────────────────────────── memory ──────────────────────────

@router.get("/memories")
def list_memories(request: Request, scope: str = "", ref_id: str = "",
                  status: str = "", q: str = "", limit: int = 200):
    auth.current_user(request)
    rows = memory.list_memories(scope, ref_id, status, q, limit)
    return {"items": rows, "total": len(rows)}


@router.post("/memories")
def create_memory(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = memory.add(payload.get("scope") or "agent",
                     payload.get("ref_id") or "",
                     payload.get("kind") or "fact",
                     payload.get("content") or "",
                     source="manual",
                     confidence=float(payload.get("confidence") or 0.7),
                     status=payload.get("status") or "pending",
                     user_id=user["id"])
    if not row:
        raise HTTPException(400, "记忆内容不能为空")
    return row


@router.patch("/memories/{memory_id}")
def update_memory(request: Request, memory_id: str,
                  payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    sets, args = [], []
    for key in ("kind", "content", "status"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(str(payload[key]))
    if "confidence" in payload:
        sets.append("confidence=?")
        args.append(float(payload["confidence"]))
    if sets:
        args.extend([services.now(), memory_id])
        db.x(f"UPDATE pf_memories SET {', '.join(sets)},updated_at=? "
             "WHERE id=?", args)
        services.audit(user["id"], "memory.update", memory_id, ",".join(payload))
    return db.one("SELECT * FROM pf_memories WHERE id=?", (memory_id,))


@router.delete("/memories/{memory_id}")
def delete_memory(request: Request, memory_id: str):
    user = auth.require(request, "editor")
    memory.delete(memory_id, user["id"])
    return {"ok": True}


@router.post("/memories/{memory_id}/approve")
@router.post("/memories/{memory_id}/reject")
def review_memory(request: Request, memory_id: str):
    user = auth.require(request, "editor")
    status = "active" if request.url.path.endswith("/approve") else "rejected"
    row = memory.set_status(memory_id, status, user["id"])
    if not row:
        raise HTTPException(404, "记忆不存在")
    return row


@router.post("/memories/consolidate")
def consolidate_memory(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    return memory.consolidate(payload.get("scope") or "agent",
                              payload.get("ref_id") or "", user["id"])


# ────────────────────────── schedules ──────────────────────────

@router.get("/schedules")
def list_schedules(request: Request):
    auth.current_user(request)
    rows = db.q("SELECT * FROM pf_schedules ORDER BY created_at DESC")
    return {"items": rows, "total": len(rows)}


@router.post("/schedules")
def create_schedule(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    name = (payload.get("name") or "").strip()
    kind = payload.get("kind") or "agent"
    if not name or not payload.get("ref_id"):
        raise HTTPException(400, "名称与目标不能为空")
    if kind not in ("agent", "workflow"):
        raise HTTPException(400, "只支持 agent 或 workflow")
    try:
        row = scheduler.create(name, kind, payload["ref_id"],
                               payload.get("cron") or "0 9 * * *",
                               bool(payload.get("enabled", True)),
                               bool(payload.get("catch_up")))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    services.audit(user["id"], "schedule.create", row["id"], name)
    return row


@router.patch("/schedules/{schedule_id}")
def update_schedule(request: Request, schedule_id: str,
                    payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = db.one("SELECT * FROM pf_schedules WHERE id=?", (schedule_id,))
    if not row:
        raise HTTPException(404, "定时任务不存在")
    sets, args = [], []
    if "name" in payload:
        sets.append("name=?")
        args.append(str(payload["name"]))
    if "cron" in payload:
        try:
            nxt = scheduler.compute_next(payload["cron"]).strftime(
                "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        sets.extend(["cron=?", "next_run=?"])
        args.extend([payload["cron"], nxt])
    for key in ("enabled", "catch_up"):
        if key in payload:
            sets.append(f"{key}=?")
            args.append(1 if payload[key] else 0)
    if "ref_id" in payload:
        sets.append("ref_id=?")
        args.append(str(payload["ref_id"]))
    if sets:
        args.append(schedule_id)
        db.x(f"UPDATE pf_schedules SET {', '.join(sets)} WHERE id=?", args)
        services.audit(user["id"], "schedule.update", schedule_id,
                       ",".join(payload))
    return db.one("SELECT * FROM pf_schedules WHERE id=?", (schedule_id,))


@router.delete("/schedules/{schedule_id}")
def delete_schedule(request: Request, schedule_id: str):
    user = auth.require(request, "editor")
    db.x("DELETE FROM pf_schedules WHERE id=?", (schedule_id,))
    services.audit(user["id"], "schedule.delete", schedule_id, "")
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run")
def run_schedule_now(request: Request, schedule_id: str):
    user = auth.require(request, "editor")
    row = db.one("SELECT * FROM pf_schedules WHERE id=?", (schedule_id,))
    if not row:
        raise HTTPException(404, "定时任务不存在")
    result = scheduler.run_schedule(row)
    services.audit(user["id"], "schedule.run", schedule_id, "")
    return result


@router.get("/schedules/{schedule_id}/runs")
def schedule_runs(request: Request, schedule_id: str):
    auth.current_user(request)
    rows = db.q("SELECT * FROM pf_schedule_runs WHERE schedule_id=? "
                "ORDER BY started_at DESC LIMIT 50", (schedule_id,))
    return {"items": rows, "total": len(rows)}