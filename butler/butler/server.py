"""大屏服务：FastAPI，只读账本 + 4 个动作接口（审批/复盘/接入/暂停）+ 静态页面。
端口默认 8420，仅监听 127.0.0.1（不出网）。"""
import sys
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from . import config, db, repo, renderer, distiller, connector, exporter, __version__

if getattr(sys, "frozen", False):                    # PyInstaller 打包态
    WEB = Path(sys._MEIPASS) / "web"
else:
    WEB = Path(__file__).resolve().parent / "web"

app = FastAPI(title="经验管家", version=__version__)
_allow = {"distill_max_input_chars": int, "reminder_hours": int,
          "digest_max_bytes": int, "active_rule_cap": int,
          "notify_bubble": bool, "poll_interval_sec": int}


@app.get("/api/state")
def state():
    s = repo.stats()
    s["version"] = __version__
    s["distill_progress"] = db.kv_get("distill_progress")
    s["digest_nonce"] = db.kv_get("digest_nonce")
    for p in s["connected"]:
        p["release_file"] = str(config.RELEASES / f"{config.project_hash(p['path'])}.md")
    return s


@app.get("/api/digest")
def digest():
    f = config.RELEASES / "default.md"
    return {"text": f.read_text("utf-8") if f.exists() else ""}


@app.post("/api/distill")
def distill():
    ok, why = distiller.start()
    return {"started": ok, "why": why}


@app.post("/api/distill/cancel")
def distill_cancel():
    distiller.cancel()
    return {"ok": True}


@app.post("/api/decide")
def decide(body: dict):
    cid = int(body.get("id", 0))
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action")
    ok = repo.decide_candidate(cid, action, edited=body.get("edited"),
                               note=str(body.get("note", "")))
    if not ok:
        raise HTTPException(409, "候选不存在/已处理/校验不过")
    renderer.render_all()
    return {"ok": True}


@app.get("/api/evidence/{cid}")
def evidence(cid: int):
    for cand in repo.candidates_list(only_pending=False):
        if cand["id"] == cid:
            return {"quotes": repo.evidence_quotes(cand["evidence"])}
    raise HTTPException(404)


@app.post("/api/connect")
def connect(body: dict):
    path = str(body.get("path", ""))
    if not connector.install(path):
        raise HTTPException(400, "路径不存在或写入失败")
    renderer.render_all()
    return {"ok": True}


@app.post("/api/skip")
def skip(body: dict):
    repo.skip_project(str(body.get("path", "")))
    return {"ok": True}


@app.post("/api/uninstall")
def uninstall(body: dict):
    path = str(body.get("path", ""))
    connector.uninstall(path)
    renderer.render_all()
    return {"ok": True}


@app.post("/api/pause")
def pause(body: dict):
    repo.set_pause(float(body.get("hours", 2)))
    return {"ok": True}


@app.post("/api/resume")
def resume():
    db.kv_set("paused_until", "")
    repo.event("resume")
    return {"ok": True}


@app.post("/api/rules/{rid}")
def rule_edit(rid: str, body: dict):
    repo.update_rule(rid, **{k: v for k, v in body.items()
                             if k in ("statement", "mode", "ex_bad", "ex_good", "status")})
    renderer.render_all()
    return {"ok": True}


@app.post("/api/profiles/{pid}")
def profile_edit(pid: str, body: dict):
    repo.update_profile(pid, **{k: v for k, v in body.items()
                                if k in ("statement", "status", "ttl_days")})
    renderer.render_all()
    return {"ok": True}


@app.post("/api/errors/resolve")
def errors_resolve(body: dict):
    repo.resolve_errors(str(body.get("kind", "")))
    return {"ok": True}


@app.post("/api/overlay")
def overlay(body: dict):
    repo.set_overlay(str(body.get("path", "")), str(body.get("text", "")))
    renderer.render_all()
    return {"ok": True}


@app.post("/api/settings")
def settings(body: dict):
    s = config.load_settings()
    for k, v in body.items():
        if k in _allow:
            try:
                s[k] = _allow[k](v)
            except (ValueError, TypeError):
                raise HTTPException(400, f"bad {k}")
    config.save_settings(s)
    return {"ok": True, "settings": s}


@app.post("/api/export")
def export():
    return {"dir": str(exporter.export_kit())}


@app.get("/")
def index():
    f = WEB / "index.html"
    if f.exists():
        return FileResponse(str(f), headers={"Cache-Control": "no-cache"})
    return JSONResponse({"error": "web/index.html missing"}, status_code=500)


def serve(port: int | None = None):
    import uvicorn
    s = config.load_settings()
    uvicorn.run(app, host="127.0.0.1", port=port or s["port"],
                log_level="warning")


def serve_thread(port: int):
    t = threading.Thread(target=serve, kwargs={"port": port}, daemon=True, name="butler-web")
    t.start()
    return t
