# -*- coding: utf-8 -*-
"""Optional federation with a zdata-agent-starter sidecar.

zdata runs agno 2.9 agents/teams/workflows behind its own HTTP + SSE API.
We do not import its code (PG/config are import-time, package names collide);
we discover and proxy it instead. Disabled unless ZDATA_BASE_URL is set.
"""
import json
import os
import time

import requests

from .. import db
from . import services

TIMEOUT = (5, 300)
CACHE = {"at": 0, "data": None}


def base_url():
    return (os.environ.get("ZDATA_BASE_URL") or "").rstrip("/")


def configured():
    return bool(base_url())


def probe(force=False):
    now = time.time()
    if not force and CACHE["data"] and now - CACHE["at"] < 15:
        return CACHE["data"]
    base = base_url()
    data = {"configured": bool(base), "online": False, "base_url": base,
            "service": "zdata-agent-starter", "items": [], "error": ""}
    if base:
        try:
            resp = requests.get(base + "/api/health", timeout=(3, 6))
            resp.raise_for_status()
            health = resp.json()
            data["online"] = True
            data["health"] = health
            data["items"] = inventory(base)
        except Exception as exc:
            data["error"] = f"{type(exc).__name__}: {exc}"[:300]
    CACHE.update(at=now, data=data)
    return data


def inventory(base):
    items = []
    for key, kind in (("agents", "agent"), ("teams", "team"),
                      ("workflows", "workflow")):
        try:
            resp = requests.get(f"{base}/api/{key}", timeout=(3, 8))
            resp.raise_for_status()
            payload = resp.json() or {}
        except Exception:
            continue
        values = payload.get(key) or []
        for value in values:
            if isinstance(value, str):
                name, desc, schema = value, "", []
            else:
                name = value.get("name") or ""
                desc = value.get("description") or ""
                schema = value.get("input_schema") or []
            if not name:
                continue
            items.append({"id": f"{kind}:{name}", "kind": kind, "name": name,
                          "description": desc[:240],
                          "input_schema": schema,
                          "status": "外部"})
    return items


def _endpoint(kind):
    return {"agent": "agents", "team": "teams",
            "workflow": "workflows"}.get(kind, "agents")


def map_event(data, state):
    """Translate one zdata SSE payload to our event schema."""
    typ = data.get("type")
    if typ == "content":
        piece = data.get("content") or ""
        state["text"] += piece
        return {"type": "delta", "text": piece}
    if typ == "step":
        content = data.get("content") or data.get("step") or ""
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)
        return {"type": "step", "name": str(content)[:80],
                "detail": ""}
    if typ == "error":
        return {"type": "error",
                "message": data.get("content") or data.get("error") or "外部运行失败",
                "code": "external"}
    if typ == "done":
        return {"type": "result", "output": state["text"],
                "session_id": data.get("session_id") or state.get("session_id")}
    return None


def stream_run(item, payload, user_id=""):
    base = base_url()
    if not base:
        yield {"type": "error", "code": "not_configured",
               "message": "未配置 ZDATA_BASE_URL"}
        return
    kind = item.get("remote_kind") or item.get("kind") or "agent"
    name = item.get("name") or ""
    started = time.time()
    run_id = services.uid("run_")
    db.x("INSERT INTO pf_runs(id,kind,ref_id,title,status,input,user_id,"
         "created_at) VALUES(?,?,?,?,?,?,?,?)",
         (run_id, "external", f"{kind}:{name}", (payload.get("message") or
          payload.get("input") or "")[:120], "running",
          services.jdump(payload), user_id or "", services.now()))
    state = {"text": "", "session_id": payload.get("session_id") or ""}
    yield {"type": "run_start", "run_id": run_id,
           "session_id": state["session_id"],
           "runtime": "zdata", "target": {"kind": kind, "name": name}}
    try:
        body = {"stream": True, "user_id": user_id or "",
                "session_id": payload.get("session_id") or ""}
        if kind == "workflow":
            body["input"] = payload.get("input") or payload.get("message") or {}
        else:
            body["message"] = payload.get("message") or ""
        resp = requests.post(f"{base}/api/{_endpoint(kind)}/{name}",
                             json=body, stream=True, timeout=TIMEOUT,
                             headers={"Accept": "text/event-stream"})
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        for raw_line in resp.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace")
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw in ("", "[DONE]"):
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if data.get("type") == "step":
                db.x("INSERT INTO pf_run_steps(id,run_id,seq,type,name,input,"
                     "output,status,ms,created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (services.uid("st_"), run_id, 1, "external",
                      str(data.get("content"))[:80], "{}", "", "ok", 0,
                      services.now()))
            event = map_event(data, state)
            if event:
                if event.get("type") == "result":
                    event["run_id"] = run_id
                    db.x("UPDATE pf_runs SET status='done',output=?,ms=?,"
                         "finished_at=? WHERE id=?",
                         (state["text"][:100000],
                          int((time.time() - started) * 1000),
                          services.now(), run_id))
                    yield event
                    yield {"type": "done", "run_id": run_id}
                    return
                yield event
        db.x("UPDATE pf_runs SET status='done',output=?,ms=?,finished_at=? "
             "WHERE id=?", (state["text"][:100000],
                            int((time.time() - started) * 1000),
                            services.now(), run_id))
        yield {"type": "result", "run_id": run_id, "output": state["text"]}
        yield {"type": "done", "run_id": run_id}
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        db.x("UPDATE pf_runs SET status='failed',error=?,finished_at=? "
             "WHERE id=?", (message[:1000], services.now(), run_id))
        yield {"type": "error", "run_id": run_id, "code": "external",
               "message": message}