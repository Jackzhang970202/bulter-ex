# -*- coding: utf-8 -*-
"""Shared helpers: JSON, pagination, state machine, serialisation."""
import json
import re
import time
import uuid

from .. import db

STATUSES = ("draft", "testing", "review", "published", "offline")
ACTIONS = {
    "submit": ("draft", "review"),
    "test": ("draft", "testing"),
    "approve": ("review", "published"),
    "publish": ("testing", "published"),
    "offline": ("published", "offline"),
    "reopen": ("offline", "draft"),
    "reject": ("review", "draft"),
}
ACTION_LABELS = {
    "submit": "提交审核", "test": "进入测试", "approve": "审核通过",
    "publish": "发布", "offline": "下线", "reopen": "重新编辑",
    "reject": "驳回",
}


def jload(value, default=None):
    if value is None or value == "":
        return default if default is not None else {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default if default is not None else {}


def jdump(value):
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def as_list(value):
    v = jload(value, [])
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def uid(prefix=""):
    return prefix + uuid.uuid4().hex[:12]


def now():
    return db.now()


def page(items, limit=50, offset=0):
    try:
        limit = max(1, min(int(limit), 200))
    except Exception:
        limit = 50
    try:
        offset = max(0, int(offset))
    except Exception:
        offset = 0
    return {"items": items[offset:offset + limit], "total": len(items),
            "limit": limit, "offset": offset}


def transition(current, action, role):
    """Return (new_status, error). Role rules: publish/offline/review are admin."""
    action = (action or "").strip()
    src_dst = ACTIONS.get(action)
    if not src_dst:
        return None, f"未知动作: {action}"
    src, dst = src_dst
    if current != src:
        return None, f"当前状态 {current} 不能执行 {ACTION_LABELS.get(action, action)}"
    if action in ("approve", "publish", "offline", "reject") and role != "admin":
        return None, "仅管理员可以审核或发布"
    return dst, ""


def parse_tags(value):
    if isinstance(value, list):
        return [str(x).strip()[:40] for x in value if str(x).strip()][:20]
    return [x.strip()[:40] for x in re.split(r"[,，;；\s]+", str(value or ""))
            if x.strip()][:20]


def mask_secret(value):
    if not value:
        return ""
    return value[:4] + "***" if len(value) > 8 else "***"


def text_excerpt(text, limit=240):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def keywords(text):
    """ASCII words plus CJK bigrams, the same family of terms used by QA."""
    text = (text or "").lower()
    latin = re.findall(r"[a-z0-9_][a-z0-9_.+-]{1,}", text)
    cjk = re.findall(r"[一-鿿]+", text)
    grams = []
    for run in cjk:
        if len(run) <= 2:
            grams.append(run)
        else:
            grams.extend(run[i:i + 2] for i in range(len(run) - 1))
    return set(latin + grams)


def score_text(query, *fields):
    terms = keywords(query)
    if not terms:
        return 0.0
    hay = " ".join(str(f or "") for f in fields).lower()
    hits = sum(1 for t in terms if t in hay)
    return hits / (len(terms) or 1)


def elapsed_ms(started):
    return int((time.time() - started) * 1000)


def agent_public(row, skills=None, tools=None):
    d = dict(row)
    for key in ("library_ids", "kb_ids", "skill_ids", "tool_ids", "tags"):
        d[key] = as_list(d.get(key))
    d["memory_enabled"] = bool(d.get("memory_enabled"))
    if skills is not None:
        d["skills"] = skills
    if tools is not None:
        d["tools"] = tools
    return d


def skill_public(row):
    d = dict(row)
    d["config"] = jload(d.get("config"), {})
    d["enabled"] = bool(d.get("enabled"))
    d["body_chars"] = len(d.get("content") or "")
    d["files"] = jload(d.get("files"), [])
    d["file_count"] = len(d["files"])
    d.pop("content", None)
    return d


def audit(user_id, action, target, detail=""):
    db.x("INSERT INTO pf_audit(id,user_id,action,target,detail,created_at) "
         "VALUES(?,?,?,?,?,?)",
         (uid("au_"), user_id or "", action, target or "",
          detail[:2000], now()))


def notify(user_id, kind, title, body="", link=""):
    db.x("INSERT INTO pf_notifications(id,user_id,kind,title,body,link,read,"
         "created_at) VALUES(?,?,?,?,?,?,0,?)",
         (uid("nt_"), user_id or "", kind, title[:200], body[:2000],
          link[:300], now()))


def setting_get(key, default=None):
    row = db.one("SELECT value FROM pf_settings WHERE key=?", (key,))
    if not row:
        return default
    return jload(row["value"], default)


def setting_set(key, value):
    db.x("INSERT INTO pf_settings(key,value,updated_at) VALUES(?,?,?) "
         "ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
         "updated_at=excluded.updated_at",
         (key, jdump(value), now()))