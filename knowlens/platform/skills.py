# -*- coding: utf-8 -*-
"""Open skill catalog: load, search, install and execute.

The upstream catalog (1137 entries) is metadata only. Installing fetches the
SKILL.md body once and records provenance; execution never downloads code.
"""
import json
import os
import re
import shutil
import time
import urllib.parse

import requests

from .. import config, db
from . import services, tools

CATALOG_PATH = os.path.join(config.ROOT, "catalog", "open_skills.json")
SKILLS_ROOT = os.path.join(config.DATA, "skills")
TREE_CACHE_DIR = os.path.join(config.DATA, "skill_tree_cache")
_TREE_CACHE = {}
VERIFIED_LICENSES = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause",
                     "ISC", "MPL-2.0", "0BSD", "Unlicense"}
_cache = {"mtime": None, "data": []}


def catalog_meta():
    data = _load()
    return {"total": len(data), "path": CATALOG_PATH,
            "generated_at": _generated_at}


_generated_at = None


def _load():
    global _generated_at
    try:
        mtime = os.path.getmtime(CATALOG_PATH)
    except OSError:
        _cache.update(mtime=None, data=[])
        return []
    if _cache["mtime"] == mtime:
        return _cache["data"]
    with open(CATALOG_PATH, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    rows = payload.get("skills") or []
    for row in rows:
        row["verified"] = row.get("license") in VERIFIED_LICENSES
    _cache.update(mtime=mtime, data=rows)
    _generated_at = payload.get("generated_at")
    return rows


def _score(entry, terms):
    if not terms:
        return 0.0
    name = (entry.get("name") or "").lower()
    title = (entry.get("title") or "").lower()
    desc = (entry.get("description") or "").lower()
    cat = (entry.get("category") or "").lower()
    tags = " ".join(entry.get("tags") or []).lower()
    score = 0.0
    for t in terms:
        if t == name:
            score += 8
        elif t in name:
            score += 5
        if t in title:
            score += 3
        if t in desc:
            score += 1.5
        if t in cat:
            score += 1
        if t in tags:
            score += 2
    return score


def search(q="", category="", kind="", license="", verified=None,
           limit=60, offset=0):
    rows = _load()
    if category:
        rows = [r for r in rows if r.get("category") == category]
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if license:
        rows = [r for r in rows if r.get("license") == license]
    if verified is not None:
        rows = [r for r in rows if bool(r.get("verified")) == bool(verified)]
    terms = [t.lower() for t in re.split(r"[\s,，]+", q or "") if t]
    if terms:
        scored = [(_score(r, terms), r) for r in rows]
        scored = [x for x in scored if x[0] > 0]
        scored.sort(key=lambda x: (-x[0], x[1].get("name") or ""))
        rows = [x[1] for x in scored]
    return services.page(rows, limit, offset)


def facets():
    rows = _load()
    cats, kinds, licenses = {}, {}, {}
    for r in rows:
        cats[r.get("category") or "general"] = cats.get(r.get("category") or "general", 0) + 1
        kinds[r.get("kind") or "prompt"] = kinds.get(r.get("kind") or "prompt", 0) + 1
        licenses[r.get("license") or "NOASSERTION"] = licenses.get(r.get("license") or "NOASSERTION", 0) + 1
    sort = lambda d: [{"name": k, "count": v}
                      for k, v in sorted(d.items(), key=lambda x: -x[1])]
    return {"categories": sort(cats), "kinds": sort(kinds),
            "licenses": sort(licenses), "total": len(rows)}


def detail(catalog_id):
    for r in _load():
        if r.get("id") == catalog_id:
            installed = db.one("SELECT id,enabled FROM pf_skills WHERE catalog_id=?",
                               (catalog_id,))
            row = dict(r)
            row["installed"] = bool(installed)
            row["installed_id"] = installed["id"] if installed else ""
            return row
    return None


def _parse_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw, body = text[3:end], text[end + 4:]
    try:
        import yaml
        meta = yaml.safe_load(raw) or {}
    except Exception:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), body


def _clean_body(body, limit=16000):
    body = re.sub(r"^#\s+.*\n", "", body.strip())
    return body.strip()[:limit]



def _branch_and_dir(entry):
    raw = entry.get("raw_url") or ""
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+/[^/]+)/([^/]+)/(.+)$",
                 raw)
    if not m:
        return "", "", ""
    repo, branch, path = m.group(1), m.group(2), m.group(3)
    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    return repo, branch, directory


def _tree_cache_path(repo, branch):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", repo + "@" + branch)
    return os.path.join(TREE_CACHE_DIR, safe + ".json")


def _github_tree(repo, branch):
    key = repo + "@" + branch
    now = time.time()
    hit = _TREE_CACHE.get(key)
    if hit and now - hit[0] < 600:
        return hit[1]
    cache_path = _tree_cache_path(repo, branch)
    disk = None
    try:
        if os.path.exists(cache_path) and now - os.path.getmtime(cache_path) < 86400:
            with open(cache_path, encoding="utf-8") as fh:
                disk = json.load(fh)
            _TREE_CACHE[key] = (now, disk)
            return disk
    except Exception:
        disk = None
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/git/trees/{branch}",
            params={"recursive": "1"}, timeout=(10, 60),
            headers={"User-Agent": "KnowLens/0.3",
                     "Accept": "application/vnd.github+json"})
        if resp.status_code != 200:
            return disk or []
        tree = (resp.json() or {}).get("tree") or []
        _TREE_CACHE[key] = (now, tree)
        try:
            os.makedirs(TREE_CACHE_DIR, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(tree, fh)
        except Exception:
            pass
        return tree
    except Exception:
        return disk or []


def _download_package(entry, skill_id):
    """Download the skill directory (SKILL.md + scripts/references) if possible."""
    repo, branch, directory = _branch_and_dir(entry)
    if not repo or not directory:
        return []
    tree = _github_tree(repo, branch)
    prefix = directory.rstrip("/") + "/"
    blobs = [x for x in tree
             if x.get("type") == "blob" and x.get("path", "").startswith(prefix)]
    if not blobs:
        return []
    root = os.path.join(SKILLS_ROOT, skill_id)
    os.makedirs(root, exist_ok=True)
    files, total = [], 0
    for item in blobs[:40]:
        rel = item["path"][len(prefix):]
        if not rel or ".." in rel.split("/"):
            continue
        raw = (f"https://raw.githubusercontent.com/{repo}/{branch}/"
               + urllib.parse.quote(item["path"]))
        try:
            resp = requests.get(raw, timeout=(10, 40),
                                headers={"User-Agent": "KnowLens/0.3"})
            if resp.status_code != 200 or len(resp.content) > 200_000:
                continue
            dest = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(resp.content)
            files.append({"path": rel, "bytes": len(resp.content)})
            total += len(resp.content)
            if total > 2_000_000:
                break
        except Exception:
            continue
    if files:
        with open(os.path.join(root, "manifest.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"source": entry.get("source_url"),
                       "repo": repo, "branch": branch,
                       "directory": directory, "files": files},
                      fh, ensure_ascii=False, indent=1)
    return files


def install(catalog_id, user_id=""):
    row = detail(catalog_id)
    if not row:
        return None, "目录中不存在该技能"
    if row.get("installed_id"):
        return db.one("SELECT * FROM pf_skills WHERE id=?", (row["installed_id"],)), ""
    body = ""
    try:
        resp = requests.get(row.get("raw_url"), timeout=(10, 40),
                            headers={"User-Agent": "KnowLens/0.3"})
        if resp.status_code == 200:
            _, body = _parse_frontmatter(resp.text)
    except Exception:
        body = ""
    if not body:
        body = row.get("description") or ""
    now = services.now()
    sid = services.uid("sk_")
    files = _download_package(row, sid)
    package_dir = os.path.join(SKILLS_ROOT, sid) if files else ""
    db.x("INSERT INTO pf_skills(id,catalog_id,name,title,description,category,"
         "kind,source_url,license,content,config,files,package_dir,enabled,"
         "version,installs,created_at,updated_at) "
         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (sid, catalog_id, row.get("name") or "", row.get("title") or "",
          row.get("description") or "", row.get("category") or "general",
          row.get("kind") or "prompt", row.get("source_url") or "",
          row.get("license") or "", _clean_body(body), "{}",
          services.jdump(files), package_dir, 1,
          row.get("repo") or "", 1, now, now))
    services.audit(user_id, "skill.install", sid, catalog_id)
    return db.one("SELECT * FROM pf_skills WHERE id=?", (sid,)), ""


def uninstall(skill_id, user_id=""):
    row = db.one("SELECT * FROM pf_skills WHERE id=?", (skill_id,))
    if not row:
        return False
    # detach from agents so no agent points at a missing skill
    for agent in db.q("SELECT id,skill_ids FROM pf_agents"):
        ids = services.as_list(agent["skill_ids"])
        if skill_id in ids:
            db.x("UPDATE pf_agents SET skill_ids=?,updated_at=? WHERE id=?",
                 (services.jdump([x for x in ids if x != skill_id]),
                  services.now(), agent["id"]))
    package = row.get("package_dir") or ""
    root = os.path.abspath(SKILLS_ROOT)
    if package and os.path.abspath(package).startswith(root + os.sep):
        shutil.rmtree(package, ignore_errors=True)
    db.x("DELETE FROM pf_skills WHERE id=?", (skill_id,))
    services.audit(user_id, "skill.uninstall", skill_id, row.get("name") or "")
    return True


def create_custom(payload, user_id=""):
    name = (payload.get("name") or "").strip()
    if not name:
        return None, "技能名称不能为空"
    kind = payload.get("kind") or "prompt"
    if kind not in ("prompt", "http", "script", "mcp"):
        return None, "不支持的技能类型"
    content = (payload.get("content") or "").strip()
    if kind == "prompt" and not content:
        return None, "提示词技能需要正文"
    sid = services.uid("sk_")
    now = services.now()
    db.x("INSERT INTO pf_skills(id,catalog_id,name,title,description,category,"
         "kind,source_url,license,content,config,enabled,version,installs,"
         "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (sid, "", name, payload.get("title") or name,
          payload.get("description") or "", payload.get("category") or "custom",
          kind, payload.get("source_url") or "", "custom", content,
          services.jdump(payload.get("config") or {}),
          0 if payload.get("enabled") is False else 1,
          payload.get("version") or "1.0", 0, now, now))
    services.audit(user_id, "skill.create", sid, name)
    return db.one("SELECT * FROM pf_skills WHERE id=?", (sid,)), ""


def import_url(url, user_id=""):
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        return None, "请提供 http(s) 链接"
    if not url.endswith("SKILL.md") and "/raw/" not in url:
        # Accept a GitHub blob URL by converting to raw
        m = re.match(r"^https://github\.com/([^/]+/[^/]+)/blob/(.+)$", url)
        if m:
            url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}"
    try:
        url = tools.safe_url(url)
    except Exception as exc:
        return None, f"链接不被允许: {exc}"
    try:
        resp = requests.get(url, timeout=(10, 40),
                            headers={"User-Agent": "KnowLens/0.3"})
        resp.raise_for_status()
    except Exception as exc:
        return None, f"下载失败: {exc}"
    meta, body = _parse_frontmatter(resp.text)
    name = str(meta.get("name") or url.rstrip("/").split("/")[-2] or "imported")
    return create_custom({
        "name": name, "title": name.replace("-", " ").title(),
        "description": str(meta.get("description") or "")[:600],
        "category": "imported", "kind": "prompt",
        "content": _clean_body(body), "source_url": url,
        "version": str(meta.get("version") or "1.0"),
    }, user_id)


def render_prompt(body, user_input, context=""):
    body = body or ""
    values = {"input": user_input or "", "context": (context or "")[:8000],
              "question": user_input or ""}
    for key, value in values.items():
        body = body.replace("{{" + key + "}}", str(value))
        body = body.replace("{" + key + "}", str(value))
    return body


def execute(row, user_input, context="", allow_high_risk=False):
    """Execute a skill row and return {ok, output, kind}."""
    kind = row.get("kind") or "prompt"
    config_ = services.jload(row.get("config"), {})
    if kind == "prompt":
        return {"ok": True, "kind": kind,
                "output": render_prompt(row.get("content"), user_input, context)}
    if kind == "http":
        url = config_.get("url") or ""
        try:
            url = tools.safe_url(render_prompt(url, user_input, context))
        except Exception as exc:
            return {"ok": False, "kind": kind, "error": str(exc)}
        method = (config_.get("method") or "GET").upper()
        body = render_prompt(config_.get("body_template") or "", user_input, context)
        try:
            resp = requests.request(
                method, url, timeout=(10, 60),
                headers=config_.get("headers") or {},
                data=body.encode("utf-8") if body else None,
                allow_redirects=False)
            return {"ok": resp.status_code < 400, "kind": kind,
                    "status": resp.status_code, "output": resp.text[:20000]}
        except Exception as exc:
            return {"ok": False, "kind": kind, "error": str(exc)}
    if kind == "script":
        op = config_.get("op") or "template"
        payload = dict(config_.get("payload") or {})
        payload.setdefault("template", row.get("content") or "")
        payload.setdefault("text", user_input or "")
        payload.setdefault("vars", {"input": user_input or ""})
        result = tools.run_script(op, payload)
        return {"ok": "error" not in result, "kind": kind,
                "output": json.dumps(result, ensure_ascii=False)}
    if kind == "mcp":
        url = config_.get("url") or ""
        try:
            url = tools.safe_url(url)
        except Exception as exc:
            return {"ok": False, "kind": kind, "error": str(exc)}
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        headers.update(config_.get("headers") or {})
        args = {}
        for key, value in (config_.get("arguments") or {}).items():
            args[key] = render_prompt(str(value), user_input, context)
        if not args:
            args = {"input": user_input}
        result = _mcp_call(url, config_.get("tool") or row.get("name"),
                           args, headers)
        return {"ok": "error" not in result, "kind": kind,
                "output": json.dumps(result, ensure_ascii=False)[:20000]}
    return {"ok": False, "kind": kind, "error": "未知技能类型"}


def _parse_mcp(resp):
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:])
                except Exception:
                    continue
        return {"error": "MCP 返回了空事件流"}
    try:
        return resp.json()
    except Exception:
        return {"error": (resp.text or "")[:1000]}


def _mcp_call(url, tool, arguments, headers):
    """Initialize a streamable-HTTP MCP session, then call one tool."""
    import uuid as _uuid
    session = requests.Session()
    init = {"jsonrpc": "2.0", "id": "init-" + _uuid.uuid4().hex[:8],
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "KnowLens", "version": "0.3"}}}
    try:
        resp = session.post(url, json=init, headers=headers, timeout=(10, 60))
        if resp.status_code >= 400:
            return {"error": f"MCP initialize HTTP {resp.status_code}"}
        sid = resp.headers.get("mcp-session-id")
        if sid:
            headers = dict(headers, **{"Mcp-Session-Id": sid})
        session.post(url, json={"jsonrpc": "2.0",
                                "method": "notifications/initialized"},
                     headers=headers, timeout=(10, 30))
        call = {"jsonrpc": "2.0", "id": "call-" + _uuid.uuid4().hex[:8],
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}}
        resp = session.post(url, json=call, headers=headers,
                            timeout=(10, 120))
        data = _parse_mcp(resp)
        if resp.status_code >= 400:
            return {"error": f"MCP HTTP {resp.status_code}",
                    "detail": data}
        if isinstance(data, dict) and data.get("error"):
            return {"error": data["error"]}
        return data
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        session.close()


def mcp_list_tools(url):
    """List tools from a remote streamable-HTTP MCP server."""
    import uuid as _uuid
    try:
        url = tools.safe_url(url)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    headers = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
    session = requests.Session()
    try:
        resp = session.post(url, json={
            "jsonrpc": "2.0", "id": "init-" + _uuid.uuid4().hex[:8],
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "KnowLens", "version": "0.3"}}},
            headers=headers, timeout=(10, 60))
        if resp.status_code >= 400:
            return {"ok": False, "error": f"initialize HTTP {resp.status_code}"}
        sid = resp.headers.get("mcp-session-id")
        if sid:
            headers = dict(headers, **{"Mcp-Session-Id": sid})
        session.post(url, json={"jsonrpc": "2.0",
                                "method": "notifications/initialized"},
                     headers=headers, timeout=(10, 30))
        resp = session.post(url, json={"jsonrpc": "2.0",
                                       "id": "list-" + _uuid.uuid4().hex[:8],
                                       "method": "tools/list", "params": {}},
                            headers=headers, timeout=(10, 60))
        data = _parse_mcp(resp)
        result = (data or {}).get("result") or {}
        items = [{"name": t.get("name"), "description": t.get("description", ""),
                  "parameters": t.get("inputSchema") or {}}
                 for t in (result.get("tools") or [])]
        return {"ok": True, "tools": items, "count": len(items)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        session.close()


def instructions(skill_ids, query="", limit=8):
    """Return prompt-skill instruction blocks, ranked by relevance."""
    if not skill_ids:
        return []
    rows = []
    for sid in skill_ids[:60]:
        row = db.one("SELECT * FROM pf_skills WHERE id=?", (sid,))
        if row and row.get("enabled") and (row.get("kind") or "prompt") == "prompt":
            rows.append(row)
    if not rows:
        return []
    terms = services.keywords(query)
    def score(row):
        return services.score_text(query, row.get("name"), row.get("description"),
                                   row.get("category"))
    ranked = sorted(rows, key=score, reverse=True)[:limit]
    blocks = []
    for row in ranked:
        blocks.append({
            "id": row["id"], "name": row["name"],
            "description": row.get("description") or "",
            "content": (row.get("content") or "")[:4000],
        })
    return blocks