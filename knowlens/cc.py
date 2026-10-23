# -*- coding: utf-8 -*-
"""Claude Code development experience: session collection, distillation into candidates, release injection (all optional, disabled by default).

- Collection: read-only JSONL sessions under ~/.claude/projects that correspond to the selected code project
- Injection: write a SessionStart hook into the project's .claude/settings.local.json (automatic backup)
"""
import glob
import json
import os
import re
import shutil
import time

from . import config, db, llm, privacy

MARKER = "knowlens_digest.py"


def cc_root():
    return os.environ.get("KNOWLENS_CC_ROOT") or os.path.expanduser(
        "~/.claude/projects")


def encode_path(path):
    """Matches Claude Code's project directory encoding: replace : \\ / with - character by character."""
    p = os.path.abspath(path)
    return p.replace(":", "-").replace("\\", "-").replace("/", "-").strip("-")


def transcript_dir(project_path):
    return os.path.join(cc_root(), encode_path(project_path))


def list_projects(space_id):
    rows = db.q("SELECT * FROM cc_projects WHERE space_id=? "
                "ORDER BY created_at DESC", (space_id,))
    for r in rows:
        d = transcript_dir(r["path"])
        files = glob.glob(os.path.join(d, "*.jsonl"))
        r["exists"] = os.path.isdir(d)
        r["session_count"] = len(files)
        last = max((os.path.getmtime(f) for f in files), default=0)
        r["latest"] = time.strftime("%Y-%m-%d %H:%M",
                                    time.localtime(last)) if last else ""
        r["injected"] = is_injected(r["path"], space_id)
    return rows


def add_project(space_id, path, name=None):
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError("项目路径不存在")
    if not os.path.isdir(transcript_dir(path)):
        if glob.glob(os.path.join(path, "*.jsonl")):
            pass  # a session directory was selected directly
        else:
            raise ValueError("未找到该项目的 Claude Code 会话目录，"
                             "请确认该项目使用过 Claude Code")
    old = db.one("SELECT id FROM cc_projects WHERE path=?", (path,))
    if old:
        return old["id"]
    pid = db.new_id("cp-")
    db.x("INSERT INTO cc_projects(id,space_id,name,path,enabled,created_at) "
         "VALUES(?,?,?,?,1,?)",
         (pid, space_id, name or os.path.basename(path), path, db.now()))
    return pid


def remove_project(project_id):
    db.x("DELETE FROM cc_projects WHERE id=?", (project_id,))


def _extract(path, max_chars=60000):
    out, total = [], 0
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            typ = obj.get("type")
            if typ not in ("user", "assistant"):
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            texts = []
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        texts.append(b.get("text") or "")
            text = "\n".join(x for x in texts if x).strip()
            if not text or text.startswith("<system-reminder>"):
                continue
            if len(text) > 8000:
                text = text[:8000] + "…"
            out.append(("用户" if typ == "user" else "助手", text))
            total += len(text)
            if total >= max_chars:
                break
    return out


DISTILL_PROMPT = (
    "你是部门经验整理助手。从下面这段 Claude Code 开发会话中，提取可长期复用的经验："
    "项目规范、技术决策、用户偏好、踩坑与教训。不要提取一次性的任务细节。"
    "每条一句话陈述，最多 4 条。\n"
    "只输出 JSON：{{\"candidates\":[{{\"kind\":\"rule|preference|lesson\","
    "\"statement\":\"...\"}}]}}"
)


def distill_session(space_id, text, title, csid, sid):
    try:
        obj, _ = llm.complete_json(
            [{"role": "system", "content": DISTILL_PROMPT},
             {"role": "user", "content": text[:16000]}], max_tokens=800)
    except Exception as e:
        print("cc distill fail:", e)
        return 0
    from . import ingest
    n = 0
    for c in (obj.get("candidates") or [])[:4]:
        st = str(c.get("statement") or "").strip()
        if len(st) < 8:
            continue
        cid = ingest.add_candidate(
            space_id, c.get("kind") or "rule", st,
            evidence=[{"cc_session": csid, "title": title,
                       "session": sid}],
            source="cc:" + sid)
        if cid:
            n += 1
    return n


def import_recent(project_id, limit=3):
    p = db.one("SELECT * FROM cc_projects WHERE id=?", (project_id,))
    if not p:
        raise ValueError("项目不存在")
    d = transcript_dir(p["path"])
    files = sorted(glob.glob(os.path.join(d, "*.jsonl")),
                   key=os.path.getmtime, reverse=True)[:limit]
    imported = []
    for f in files:
        if db.one("SELECT id FROM cc_sessions WHERE file=?", (f,)):
            continue
        msgs = _extract(f)
        if not msgs:
            continue
        text = privacy.mask("\n".join(f"{r}：{t}" for r, t in msgs),
                            p["space_id"])
        title = next((t for r, t in msgs if r == "用户"), "开发会话")[:40]
        sid = os.path.splitext(os.path.basename(f))[0]
        csid = db.new_id("cs-")
        db.x("INSERT INTO cc_sessions(id,project_id,space_id,file,session_id,"
             "title,summary,message_count,imported_at) "
             "VALUES(?,?,?,?,?,?,?,?,?)",
             (csid, p["id"], p["space_id"], f, sid, title, text[:300],
              len(msgs), db.now()))
        n = distill_session(p["space_id"], text, title, csid, sid)
        imported.append({"session_id": csid, "title": title,
                         "messages": len(msgs), "candidates": n})
    db.x("UPDATE cc_projects SET last_scan=? WHERE id=?",
         (db.now(), project_id))
    return imported


def settings_path(project_path):
    return os.path.join(project_path, ".claude", "settings.local.json")


def is_injected(project_path, space_id):
    sp = settings_path(project_path)
    if not os.path.isfile(sp):
        return False
    try:
        with open(sp, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    for entry in (data.get("hooks") or {}).get("SessionStart") or []:
        for h in entry.get("hooks") or []:
            cmd = h.get("command") or ""
            if MARKER in cmd and f"--space {space_id}" in cmd:
                return True
    return False


def inject(project_id, action):
    p = db.one("SELECT * FROM cc_projects WHERE id=?", (project_id,))
    if not p:
        raise ValueError("项目不存在")
    sp = settings_path(p["path"])
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    data = {}
    if os.path.isfile(sp):
        shutil.copyfile(sp, sp + ".knowlens.bak")
        try:
            with open(sp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    hooks = data.setdefault("hooks", {})
    ss = hooks.setdefault("SessionStart", [])
    # Only remove hooks written by this product; keep the user's other hooks
    for entry in ss:
        entry["hooks"] = [h for h in (entry.get("hooks") or [])
                          if MARKER not in (h.get("command") or "")]
    ss[:] = [e for e in ss if e.get("hooks")]
    if action == "install":
        cmd = (f'python "{os.path.join(config.ROOT, "scripts", "knowlens_digest.py")}"'
               f" --space {p['space_id']}")
        ss.append({"matcher": "startup|resume|clear|compact|fork",
                   "hooks": [{"type": "command", "command": cmd,
                              "timeout": 15}]})
    if not ss:
        hooks.pop("SessionStart", None)
    if not hooks:
        data.pop("hooks", None)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"ok": True, "settings": sp, "action": action,
            "injected": action == "install"}