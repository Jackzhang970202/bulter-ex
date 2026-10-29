# -*- coding: utf-8 -*-
"""Tool registry with server-side permission boundaries.

Tools are the only way a model can affect the outside world. Every handler
receives a context dict (user, space, run) and every invocation is recorded by
the runtime, never by the tool itself.
"""
import ast
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse

import requests

from .. import config

WORK_DIR = os.path.join(config.DATA, "platform_work")
MAX_HTTP_BYTES = 200_000


def _schema(props=None, required=None):
    return {"type": "object", "properties": props or {},
            "required": required or [], "additionalProperties": False}


TOOLS = {
    "knowledge_search": {
        "description": "Search the bound knowledge space and return page-level snippets with document and page numbers.",
        "parameters": _schema({
            "query": {"type": "string", "description": "Search query"},
            "top_k": {"type": "integer", "description": "Documents to inspect (1-8)"},
        }, ["query"]),
        "risk": "low",
        "handler": None,  # wired below
    },
    "calculator": {
        "description": "Evaluate a pure arithmetic expression. No variables, no function calls except round/min/max/abs.",
        "parameters": _schema({"expression": {"type": "string"}}, ["expression"]),
        "risk": "low",
        "handler": None,
    },
    "json_query": {
        "description": "Read a value from JSON using a dotted path such as data.items.0.name.",
        "parameters": _schema({
            "data": {"type": "string", "description": "JSON text"},
            "path": {"type": "string"},
        }, ["data", "path"]),
        "risk": "low",
        "handler": None,
    },
    "current_time": {
        "description": "Return the current local date and time.",
        "parameters": _schema({}),
        "risk": "low",
        "handler": None,
    },
    "http_request": {
        "description": "Call an external HTTP(S) API. Private and loopback addresses are blocked.",
        "parameters": _schema({
            "url": {"type": "string"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"]},
            "headers": {"type": "object"},
            "body": {"type": "string"},
        }, ["url"]),
        "risk": "high",
        "handler": None,
    },
    "file_read": {
        "description": "Read a UTF-8 text file inside the platform workspace (relative path only).",
        "parameters": _schema({"path": {"type": "string"}}, ["path"]),
        "risk": "high",
        "handler": None,
    },
    "call_agent": {
        "description": "Delegate a self-contained subtask to another published agent and return its answer. Depth-limited.",
        "parameters": _schema({
            "agent_id": {"type": "string"},
            "message": {"type": "string"},
        }, ["agent_id", "message"]),
        "risk": "medium",
        "handler": None,
    },
    "web_search": {
        "description": "Look up short factual summaries from the public web (DuckDuckGo instant answers).",
        "parameters": _schema({"query": {"type": "string"}}, ["query"]),
        "risk": "low",
        "handler": None,
    },
}


def list_tools():
    return [{"id": name, "name": name, "description": t["description"],
             "parameters": t["parameters"], "risk": t["risk"]}
            for name, t in TOOLS.items()]


def schemas(names):
    return [{"name": n, "description": TOOLS[n]["description"],
             "parameters": TOOLS[n]["parameters"]}
            for n in names if n in TOOLS]


def is_high_risk(name):
    return (TOOLS.get(name) or {}).get("risk") == "high"


# ────────────────────────── guards ──────────────────────────

def _private_host(host):
    if os.environ.get("KNOWLENS_ALLOW_PRIVATE_HTTP") == "1":
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except Exception:
            return True
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            return True
    return False


def safe_url(url):
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("只允许 http/https")
    if not parsed.hostname or _private_host(parsed.hostname):
        raise ValueError("目标地址不允许访问（内网/回环）")
    return url


def safe_join(root, relative):
    if not relative or os.path.isabs(relative) or ".." in relative.split("/"):
        raise ValueError("只允许工作目录内的相对路径")
    root_abs = os.path.abspath(root)
    target = os.path.abspath(os.path.join(root_abs, relative))
    if not target.startswith(root_abs + os.sep):
        raise ValueError("路径越界")
    return target


# ────────────────────────── handlers ──────────────────────────

def _knowledge_search(args, ctx):
    from .. import kb
    from ..qa import candidate_docs
    space_id = ctx.get("space_id") or ""
    if not space_id:
        return {"error": "当前智能体未绑定知识空间"}
    query = str(args.get("query") or "")
    top_k = max(1, min(int(args.get("top_k") or 4), 8))
    docs = candidate_docs(space_id, ctx.get("library_ids") or [], None)
    if not docs:
        return {"results": [], "note": "知识空间没有可用文档"}
    from ..qa import _rank_docs
    ranked = _rank_docs(query, docs)[:top_k]
    terms = set(re.findall(r"[a-z0-9_]{2,}|[一-鿿]{2,4}", query.lower()))
    results = []
    store = kb.get_store(space_id)
    for doc in ranked:
        pages = store.get_pages(doc["doc_id"]) or []
        scored = []
        for p in pages:
            text = p.get("markdown") or ""
            score = sum(1 for t in terms if t in text.lower())
            if score:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        for _, p in scored[:2]:
            excerpt = re.sub(r"\s+", " ", p.get("markdown") or "")[:900]
            results.append({"doc": doc["name"], "doc_id": doc["doc_id"],
                            "page": p.get("page_index") or 0,
                            "excerpt": excerpt, "kind": "direct"})
    return {"results": results[:top_k * 2]}


_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                  ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
                  ast.Pow, ast.USub, ast.UAdd, ast.Load, ast.Call, ast.Name)


def safe_eval(expression):
    tree = ast.parse(str(expression), mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"不允许的表达式: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in ("round", "min", "max", "abs"):
            raise ValueError(f"不允许的名称: {node.id}")
    return eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}},
                {"round": round, "min": min, "max": max, "abs": abs})


def _calculator(args, ctx):
    try:
        return {"result": safe_eval(args.get("expression"))}
    except Exception as exc:
        return {"error": str(exc)}


def json_path(data, path):
    cur = data
    for part in [p for p in str(path).split(".") if p != ""]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(part)
    return cur


def _json_query(args, ctx):
    try:
        data = json.loads(args.get("data") or "{}")
        return {"result": json_path(data, args.get("path"))}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _current_time(args, ctx):
    return {"now": time.strftime("%Y-%m-%d %H:%M:%S"),
            "weekday": time.strftime("%A")}


def _http_request(args, ctx):
    url = safe_url(args.get("url"))
    method = (args.get("method") or "GET").upper()
    headers = args.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    body = args.get("body")
    resp = requests.request(method, url, headers=headers,
                            data=body.encode("utf-8") if body else None,
                            timeout=(10, 60), allow_redirects=False)
    text = resp.text[:MAX_HTTP_BYTES]
    return {"status": resp.status_code, "body": text,
            "content_type": resp.headers.get("content-type", "")}


def _web_search(args, ctx):
    q = urllib.parse.quote(str(args.get("query") or ""))
    url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
    try:
        resp = requests.get(url, timeout=(10, 30),
                            headers={"User-Agent": "KnowLens/0.3"})
        data = resp.json()
        related = [{"text": x.get("Text", ""), "url": x.get("FirstURL", "")}
                   for x in (data.get("RelatedTopics") or [])
                   if isinstance(x, dict) and x.get("Text")][:8]
        return {"abstract": data.get("AbstractText") or "",
                "results": related}
    except Exception as exc:
        return {"error": f"搜索失败: {exc}"}


def _file_read(args, ctx):
    os.makedirs(WORK_DIR, exist_ok=True)
    path = safe_join(WORK_DIR, args.get("path"))
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return {"content": fh.read(MAX_HTTP_BYTES)}


TOOLS["knowledge_search"]["handler"] = _knowledge_search
TOOLS["calculator"]["handler"] = _calculator
TOOLS["json_query"]["handler"] = _json_query
TOOLS["current_time"]["handler"] = _current_time
TOOLS["http_request"]["handler"] = _http_request
TOOLS["file_read"]["handler"] = _file_read
TOOLS["web_search"]["handler"] = _web_search
TOOLS["call_agent"]["handler"] = None  # wired in runtime to avoid a circular import


def _call_agent(args, ctx):
    from . import runtime
    depth = int(ctx.get("delegation_depth") or 0)
    if depth >= 2:
        return {"error": "委派层级过深，已停止"}
    agent = runtime.get_agent(args.get("agent_id") or "")
    if not agent:
        return {"error": "目标智能体不存在"}
    if agent.get("status") != "published":
        return {"error": "只能委派给已发布的智能体"}
    if agent["id"] == ctx.get("agent_id"):
        return {"error": "不能委派给自身"}
    result = runtime.agent_answer(
        agent, str(args.get("message") or ""),
        ctx.get("user_id") or "", depth=depth + 1)
    return {"agent": agent["name"], "answer": result["text"][:8000]}


TOOLS["call_agent"]["handler"] = _call_agent


def execute(name, args, ctx=None):
    tool = TOOLS.get(name)
    if not tool:
        return {"error": f"未知工具: {name}"}
    try:
        result = tool["handler"](args or {}, ctx or {})
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > 20000:
        result = {"truncated": True, "preview": text[:20000]}
    return result


# Audited operations used by the `script` skill kind (never arbitrary Python).

def _op_json_query(payload):
    return json_path(json.loads(payload.get("data") or "{}"),
                     payload.get("path") or "")


def _op_regex_extract(payload):
    m = re.search(payload.get("pattern") or "", payload.get("text") or "")
    return {"match": m.group(0) if m else "", "groups": list(m.groups()) if m else []}


def _op_template(payload):
    text = payload.get("template") or ""
    for k, v in (payload.get("vars") or {}).items():
        text = text.replace("{" + str(k) + "}", str(v))
    return {"result": text}


SCRIPT_OPS = {
    "json_query": _op_json_query,
    "regex_extract": _op_regex_extract,
    "template": _op_template,
}


def run_script(op, payload):
    fn = SCRIPT_OPS.get(op)
    if not fn:
        return {"error": f"不支持的脚本操作: {op}"}
    try:
        return {"result": fn(payload or {})}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}