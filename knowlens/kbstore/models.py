# -*- coding: utf-8 -*-
"""Configurable model sources (LLM / Embedding / Rerank / VLM / ASR).

Mirrors the source project's model settings: every capability references a
named model record with provider, base URL, API key and model name. Records
live in ``model_configs``; API keys are stored server-side and never returned
by the API (only a masked hint is exposed).

Environment variables remain a fallback so an existing deployment keeps
working without creating records.
"""
import json
import os

from .. import config, db
from ..platform import services

KINDS = ("chat", "embedding", "rerank", "vllm", "asr")
KIND_LABELS = {"chat": "对话模型", "embedding": "嵌入模型",
               "rerank": "重排模型", "vllm": "视觉模型", "asr": "语音模型"}
ALIASES = {"llm": "chat", "vlm": "vllm"}
PROVIDERS = ("openai", "anthropic", "azure", "ollama", "custom")

DEFAULTS = {
    "chat": {"provider": "openai", "model_name": config.MIMO.get("model", ""),
            "base_url": config.MIMO.get("base_url", ""),
            "api_key": config.MIMO.get("api_key", "")},
    "embedding": {"provider": "openai",
                  "model_name": os.environ.get("EMBEDDING_MODEL", ""),
                  "base_url": os.environ.get("EMBEDDING_BASE_URL", ""),
                  "api_key": os.environ.get("EMBEDDING_API_KEY", "")},
    "rerank": {"provider": "openai",
               "model_name": os.environ.get("RERANK_MODEL", ""),
               "base_url": os.environ.get("RERANK_BASE_URL", ""),
               "api_key": os.environ.get("RERANK_API_KEY", "")},
    "vllm": {"provider": "openai",
            "model_name": os.environ.get("VLM_MODEL", ""),
            "base_url": os.environ.get("VLM_BASE_URL", ""),
            "api_key": os.environ.get("VLM_API_KEY", "")},
    "asr": {"provider": "openai",
            "model_name": os.environ.get("ASR_MODEL", ""),
            "base_url": os.environ.get("ASR_BASE_URL", ""),
            "api_key": os.environ.get("ASR_API_KEY", "")},
}


def _mask(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * 8


def public(row):
    data = dict(row)
    data["kind"] = ALIASES.get(data.get("kind"), data.get("kind"))
    data["api_key"] = _mask(data.get("api_key"))
    data["has_key"] = bool(row.get("api_key"))
    data["extra"] = services.jload(row.get("extra"), {})
    data["enabled"] = bool(row.get("enabled"))
    data["is_default"] = bool(row.get("is_default"))
    data["supports_vision"] = bool(row.get("supports_vision"))
    data["dimension"] = int(row.get("dimension") or 0)
    data["context_window"] = int(row.get("context_window") or 0)
    data["source"] = row.get("source") or "custom"
    data["kind_label"] = KIND_LABELS.get(data.get("kind"), data.get("kind"))
    return data


def list_models(kind=""):
    if kind:
        rows = db.q("SELECT * FROM model_configs WHERE kind=? "
                    "ORDER BY is_default DESC, created_at", (kind,))
    else:
        rows = db.q("SELECT * FROM model_configs ORDER BY kind, "
                    "is_default DESC, created_at")
    return [public(row) for row in rows]


def get_model(model_id):
    return db.one("SELECT * FROM model_configs WHERE id=?", (model_id,))


def create_model(payload):
    kind = (payload.get("kind") or "").strip()
    kind = ALIASES.get(kind, kind)
    if kind not in KINDS:
        raise ValueError("模型类型必须是 " + "/".join(KINDS))
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("模型名称不能为空")
    provider = (payload.get("provider") or "openai").strip()
    if provider not in PROVIDERS:
        raise ValueError("不支持的 provider：" + provider)
    mid = services.uid("md_")
    now = services.now()
    is_default = 1 if payload.get("is_default") else 0
    if is_default:
        db.x("UPDATE model_configs SET is_default=0 WHERE kind=?", (kind,))
    db.x(
        "INSERT INTO model_configs(id,kind,name,provider,base_url,api_key,"
        "model_name,dimension,context_window,supports_vision,source,extra,"
        "enabled,is_default,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mid, kind, name, provider, (payload.get("base_url") or "").strip(),
         payload.get("api_key") or "", (payload.get("model_name") or "").strip(),
         int(payload.get("dimension") or 0),
         int(payload.get("context_window") or 0),
         1 if payload.get("supports_vision") else 0,
         payload.get("source") or "custom",
         json.dumps(payload.get("extra") or {}, ensure_ascii=False),
         1 if payload.get("enabled", True) else 0, is_default, now, now))
    return public(get_model(mid))


def update_model(model_id, payload):
    row = get_model(model_id)
    if not row:
        return None
    fields, args = [], []

    def put(key, value):
        fields.append(f"{key}=?")
        args.append(value)

    for key in ("name", "provider", "base_url", "model_name", "source"):
        if key in payload:
            put(key, str(payload[key]).strip())
    for key in ("dimension", "context_window"):
        if key in payload:
            put(key, int(payload[key] or 0))
    if "supports_vision" in payload:
        put("supports_vision", 1 if payload["supports_vision"] else 0)
    if payload.get("api_key"):
        put("api_key", payload["api_key"])
    if "extra" in payload:
        put("extra", json.dumps(payload.get("extra") or {},
                                ensure_ascii=False))
    if "enabled" in payload:
        put("enabled", 1 if payload["enabled"] else 0)
    if "is_default" in payload:
        if payload["is_default"]:
            db.x("UPDATE model_configs SET is_default=0 WHERE kind=?",
                 (row["kind"],))
        put("is_default", 1 if payload["is_default"] else 0)
    if not fields:
        return public(row)
    args.extend([services.now(), model_id])
    db.x(f"UPDATE model_configs SET {','.join(fields)},updated_at=? "
         "WHERE id=?", args)
    return public(get_model(model_id))


def delete_model(model_id):
    row = get_model(model_id)
    if not row:
        return False
    db.x("DELETE FROM model_configs WHERE id=?", (model_id,))
    return True


def resolve(kind, model_id=""):
    """Return an effective model config: explicit id > default row > env."""
    kind = ALIASES.get(kind, kind)
    if model_id:
        row = get_model(model_id)
        if row and row.get("enabled"):
            data = _effective(row)
            fallback = DEFAULTS.get(kind) or {}
            for key in ("base_url", "api_key", "model_name"):
                if not data.get(key):
                    data[key] = fallback.get(key, "")
            return data
    row = db.one("SELECT * FROM model_configs WHERE kind=? AND is_default=1 "
                 "AND enabled=1 LIMIT 1", (kind,))
    if row:
        return _effective(row)
    fallback = dict(DEFAULTS.get(kind) or {})
    fallback.update({"id": "", "kind": kind, "name": kind + "-env",
                     "enabled": True, "is_default": False, "extra": {}})
    return fallback


def _effective(row):
    data = dict(row)
    data["extra"] = services.jload(row.get("extra"), {})
    return data


def configured(kind):
    model = resolve(kind)
    return bool(model.get("base_url") and model.get("model_name")
                and model.get("api_key"))


# ────────────────────────── runtime calls ──────────────────────────

def embed(texts, model_id=""):
    """OpenAI-compatible embeddings. Returns list[list[float]]."""
    import requests
    model = resolve("embedding", model_id)
    if not (model.get("base_url") and model.get("model_name")):
        raise RuntimeError("未配置嵌入模型")
    url = model["base_url"].rstrip("/") + "/embeddings"
    response = requests.post(
        url, timeout=60,
        headers={"Authorization": f"Bearer {model['api_key']}",
                 "Content-Type": "application/json"},
        json={"model": model["model_name"], "input": list(texts)})
    response.raise_for_status()
    payload = response.json()
    return [item["embedding"] for item in payload.get("data", [])]


def rerank(query, documents, model_id="", top_n=None):
    """Cohere/Jina-style ``/rerank`` endpoint."""
    import requests
    model = resolve("rerank", model_id)
    if not (model.get("base_url") and model.get("model_name")):
        raise RuntimeError("未配置重排模型")
    url = model["base_url"].rstrip("/") + "/rerank"
    body = {"model": model["model_name"], "query": query,
            "documents": list(documents)}
    if top_n:
        body["top_n"] = int(top_n)
    response = requests.post(
        url, timeout=60,
        headers={"Authorization": f"Bearer {model['api_key']}",
                 "Content-Type": "application/json"}, json=body)
    response.raise_for_status()
    return response.json().get("results", [])


def chat(messages, model_id="", temperature=0.2, max_tokens=512,
         kind="chat"):
    """OpenAI-compatible chat (Anthropic messages API when configured)."""
    import requests
    model = resolve(kind, model_id)
    if not (model.get("base_url") and model.get("model_name")):
        raise RuntimeError("未配置对话模型")
    provider = (model.get("provider") or "openai").lower()
    base = model["base_url"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    if provider == "anthropic":
        url = base + "/messages"
        headers["x-api-key"] = model.get("api_key") or ""
        headers["anthropic-version"] = "2023-06-01"
        system = "\n".join(str(m.get("content") or "")
                           for m in messages if m.get("role") == "system")
        payload = {"model": model["model_name"],
                   "max_tokens": int(max_tokens),
                   "temperature": float(temperature),
                   "messages": [m for m in messages
                                if m.get("role") != "system"]}
        if system:
            payload["system"] = system
    else:
        url = base + "/chat/completions"
        headers["Authorization"] = "Bearer " + (model.get("api_key") or "")
        payload = {"model": model["model_name"],
                   "temperature": float(temperature),
                   "max_tokens": int(max_tokens), "messages": messages}
    response = requests.post(url, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    data = response.json()
    if provider == "anthropic":
        text = "".join(block.get("text") or "" for block in
                       (data.get("content") or []) if isinstance(block, dict))
        usage = data.get("usage") or {}
        return {"text": text,
                "usage": {"in": usage.get("input_tokens", 0),
                          "out": usage.get("output_tokens", 0)}}
    choice = (data.get("choices") or [{}])[0]
    usage = data.get("usage") or {}
    return {"text": (choice.get("message") or {}).get("content") or "",
            "usage": {"in": usage.get("prompt_tokens", 0),
                      "out": usage.get("completion_tokens", 0)}}


def describe_image(image_bytes, prompt="请描述这张图片", model_id=""):
    """OpenAI-compatible vision chat."""
    import base64
    import requests
    model = resolve("vllm", model_id)
    if not (model.get("base_url") and model.get("model_name")):
        raise RuntimeError("未配置视觉模型")
    url = model["base_url"].rstrip("/") + "/chat/completions"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    response = requests.post(
        url, timeout=120,
        headers={"Authorization": f"Bearer {model['api_key']}",
                 "Content-Type": "application/json"},
        json={"model": model["model_name"], "messages": [{
            "role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + encoded}}]}]})
    response.raise_for_status()
    choices = response.json().get("choices") or [{}]
    return (choices[0].get("message") or {}).get("content", "")


def test_model(model_id, payload=None):
    """Connectivity probe with latency and a readable preview.

    Never returns the API key. The payload may carry ``input`` (prompt or
    sample text), ``documents`` (rerank candidates), ``temperature`` and
    ``max_tokens``.
    """
    import time
    payload = payload or {}
    row = get_model(model_id)
    if not row:
        return {"ok": False, "error": "模型不存在", "elapsed_ms": 0}
    kind = ALIASES.get(row["kind"], row["kind"])
    started = time.time()
    info = {"id": row["id"], "name": row["name"], "kind": kind,
            "provider": row.get("provider") or "",
            "model_name": row.get("model_name") or "",
            "base_url": row.get("base_url") or ""}
    text = str(payload.get("input") or "").strip() or "请用一句话介绍你自己。"
    try:
        if kind == "embedding":
            vectors = embed([text], model_id)
            elapsed = int((time.time() - started) * 1000)
            return {"ok": bool(vectors), "elapsed_ms": elapsed, **info,
                    "metrics": {"dimension": len(vectors[0]) if vectors else 0,
                                "vectors": len(vectors)},
                    "preview": str((vectors[0][:8] if vectors else []))}
        if kind == "rerank":
            documents = payload.get("documents") or [text, "另一个候选文档"]
            if isinstance(documents, str):
                documents = [d.strip() for d in documents.splitlines() if d.strip()]
            results = rerank(text, documents, model_id)
            elapsed = int((time.time() - started) * 1000)
            return {"ok": True, "elapsed_ms": elapsed, **info,
                    "metrics": {"documents": len(documents),
                                "returned": len(results)},
                    "preview": str(results[:3])}
        if kind == "vllm":
            image = payload.get("image")
            if image:
                import base64
                output = describe_image(base64.b64decode(image), text, model_id)
            else:
                output = chat([{"role": "user", "content": text}], model_id,
                              float(payload.get("temperature") or 0.2),
                              int(payload.get("max_tokens") or 256), "vllm")["text"]
            elapsed = int((time.time() - started) * 1000)
            return {"ok": bool(output), "elapsed_ms": elapsed, **info,
                    "preview": output[:800]}
        if kind == "asr":
            return {"ok": True, "elapsed_ms": int((time.time() - started) * 1000),
                    **info, "preview": "语音模型由本机音频管线调用，无需远程探测。"}
        output = chat([
            {"role": "system", "content": "你是连通性测试助手，请简短回答。"},
            {"role": "user", "content": text},
        ], model_id, float(payload.get("temperature") or 0.2),
            int(payload.get("max_tokens") or 256), "chat")
        elapsed = int((time.time() - started) * 1000)
        return {"ok": bool(output.get("text")), "elapsed_ms": elapsed, **info,
                "metrics": output.get("usage") or {},
                "preview": (output.get("text") or "")[:800]}
    except Exception as exc:
        return {"ok": False, "elapsed_ms": int((time.time() - started) * 1000),
                **info, "error": f"{type(exc).__name__}: {exc}"}


def _tiny_png():
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4"
        "nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")