# -*- coding: utf-8 -*-
"""Read-only client for the official MCP server registry.

Only streamable-HTTP / SSE remotes are offered, because the platform does not
execute local stdio commands. Results are cached for five minutes.
"""
import json
import time

import requests

BASE = "https://registry.modelcontextprotocol.io/v0/servers"
CACHE = {}
TTL = 300


def _fetch(params):
    key = json.dumps(params, sort_keys=True, ensure_ascii=False)
    hit = CACHE.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    resp = requests.get(BASE, params=params, timeout=(10, 30),
                        headers={"User-Agent": "KnowLens/0.3"})
    resp.raise_for_status()
    data = resp.json()
    CACHE[key] = (time.time(), data)
    return data


def _entry(server, meta):
    remotes = [r for r in (server.get("remotes") or [])
               if r.get("type") in ("streamable-http", "sse") and r.get("url")]
    if not remotes:
        return None
    return {
        "name": server.get("name") or "",
        "title": server.get("title") or server.get("name") or "",
        "description": (server.get("description") or "")[:800],
        "version": server.get("version") or "",
        "repository": (server.get("repository") or {}).get("url", "")
        if isinstance(server.get("repository"), dict) else "",
        "url": remotes[0]["url"],
        "transport": remotes[0]["type"],
        "remotes": remotes,
        "official": bool((meta or {}).get("io.modelcontextprotocol.registry/official")),
    }


def search(q="", limit=40, cursor=""):
    params = {"limit": max(1, min(int(limit), 100))}
    if q:
        params["search"] = q
    if cursor:
        params["cursor"] = cursor
    data = _fetch(params)
    seen, items = set(), []
    for row in data.get("servers") or []:
        entry = _entry(row.get("server") or {}, row.get("_meta") or {})
        if not entry or entry["name"] in seen:
            continue
        seen.add(entry["name"])
        items.append(entry)
    return {"items": items, "count": len(items),
            "next_cursor": (data.get("metadata") or {}).get("nextCursor", ""),
            "source": BASE}


def tools(url):
    """List tools exposed by one remote MCP server."""
    from . import skills
    return skills.mcp_list_tools(url)