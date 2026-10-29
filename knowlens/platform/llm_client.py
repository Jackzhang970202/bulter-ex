# -*- coding: utf-8 -*-
"""Provider-neutral chat client with tool calls and streaming.

Two wire protocols are supported without an SDK:

- OpenAI-compatible  POST {base}/chat/completions
- Anthropic-compatible POST {base}/v1/messages

Both are normalised to one internal message/tool/event schema so agent,
team, workflow and skill runtimes do not care which channel is active.
"""
import json
import os
import re
import time

import requests

from .. import config, db

TIMEOUT = (20, 300)
MAX_TOOL_RESULT = 12000


class LLMError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def normalize_model(model):
    """`qwen3.8-flash[1m]` is not a valid model id; the suffix is a client hint."""
    model = (model or "").strip()
    return re.sub(r"\[[^\]]*\]\s*$", "", model)


def _channels():
    out = []
    for name, ch in (("primary", config.MIMO), ("fallback", config.MAAS)):
        if not ch.get("api_key") or not ch.get("base_url"):
            continue
        out.append({
            "name": name,
            "protocol": ch.get("protocol") or "openai",
            "model": normalize_model(ch.get("model")),
            "base_url": ch["base_url"].rstrip("/"),
            "api_key": ch["api_key"],
        })
    return out


def available_channels():
    return [{"name": c["name"], "protocol": c["protocol"],
             "model": c["model"], "configured": True} for c in _channels()]


def stub_mode():
    return os.environ.get("KNOWLENS_LLM_STUB") == "1" or not _channels()


def _stub(messages, tools):
    last = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last = m.get("content") or ""
            break
    text = "（测试通道）已收到：" + re.sub(r"\s+", " ", last)[:400]
    return {"text": text, "tool_calls": [], "usage": {"in": len(last) // 3,
                                                      "out": len(text) // 3},
            "channel": "stub"}


def _headers(ch):
    if ch["protocol"] == "anthropic":
        return {"Content-Type": "application/json", "x-api-key": ch["api_key"],
                "anthropic-version": "2023-06-01"}
    return {"Content-Type": "application/json",
            "Authorization": "Bearer " + ch["api_key"]}


def _openai_messages(messages):
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""),
                        "content": str(m.get("content") or "")[:MAX_TOOL_RESULT]})
            continue
        row = {"role": role, "content": m.get("content") or ""}
        if m.get("tool_calls"):
            row["content"] = row["content"] or None
            row["tool_calls"] = [{
                "id": c.get("id") or ("call_" + str(i)),
                "type": "function",
                "function": {"name": c["name"],
                             "arguments": json.dumps(c.get("arguments") or {},
                                                     ensure_ascii=False)},
            } for i, c in enumerate(m["tool_calls"])]
        out.append(row)
    return out


def _anthropic_messages(messages):
    system = "\n\n".join(str(m.get("content") or "")
                         for m in messages if m.get("role") == "system")
    out = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "tool":
            block = {"type": "tool_result",
                     "tool_use_id": m.get("tool_call_id", ""),
                     "content": str(m.get("content") or "")[:MAX_TOOL_RESULT]}
            if out and out[-1]["role"] == "user":
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        content = []
        if m.get("content"):
            content.append({"type": "text", "text": str(m["content"])})
        for c in (m.get("tool_calls") or []):
            content.append({"type": "tool_use", "id": c.get("id") or "",
                            "name": c["name"], "input": c.get("arguments") or {}})
        if not content:
            content = [{"type": "text", "text": ""}]
        role = "assistant" if role == "assistant" else "user"
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(content)
        else:
            out.append({"role": role, "content": content})
    return system, out


def _payload(ch, messages, tools, temperature, max_tokens, stream, json_mode):
    if ch["protocol"] == "anthropic":
        system, msgs = _anthropic_messages(messages)
        body = {"model": ch["model"], "messages": msgs,
                "max_tokens": max_tokens, "temperature": temperature,
                "stream": stream}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                              "input_schema": t.get("parameters") or
                              {"type": "object", "properties": {}}}
                             for t in tools]
        return body
    body = {"model": ch["model"], "messages": _openai_messages(messages),
            "temperature": temperature, "max_tokens": max_tokens,
            "stream": stream}
    if tools:
        body["tools"] = [{"type": "function",
                          "function": {"name": t["name"],
                                       "description": t.get("description", ""),
                                       "parameters": t.get("parameters") or
                                       {"type": "object", "properties": {}}}}
                         for t in tools]
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def _raise_for_status(resp):
    if resp.status_code < 400:
        return
    try:
        detail = resp.text[:600]
    except Exception:
        detail = ""
    code = {401: "auth", 403: "auth", 404: "model_not_found", 429: "rate_limit",
            408: "timeout"}.get(resp.status_code, "internal")
    raise LLMError(code, f"HTTP {resp.status_code}: {detail}",
                   retryable=resp.status_code >= 500 or resp.status_code == 429)


def _post(ch, body, stream):
    url = (ch["base_url"] + "/v1/messages" if ch["protocol"] == "anthropic"
           else ch["base_url"] + "/chat/completions")
    resp = requests.post(url, headers=_headers(ch), json=body,
                         timeout=TIMEOUT, stream=stream)
    _raise_for_status(resp)
    return resp


def _parse_openai(resp):
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {"_raw": fn.get("arguments")}
        calls.append({"id": c.get("id") or "", "name": fn.get("name", ""),
                      "arguments": args})
    usage = data.get("usage") or {}
    return {"text": msg.get("content") or "", "tool_calls": calls,
            "usage": {"in": usage.get("prompt_tokens") or 0,
                      "out": usage.get("completion_tokens") or 0}}


def _parse_anthropic(resp):
    data = resp.json()
    text_parts, calls = [], []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            calls.append({"id": block.get("id") or "", "name": block.get("name", ""),
                          "arguments": block.get("input") or {}})
    usage = data.get("usage") or {}
    return {"text": "".join(text_parts), "tool_calls": calls,
            "usage": {"in": usage.get("input_tokens") or 0,
                      "out": usage.get("output_tokens") or 0}}


def complete(messages, tools=None, model=None, temperature=0.2,
             max_tokens=4000, json_mode=False):
    """Non-streaming completion with channel fallback and usage accounting."""
    if stub_mode():
        return _stub(messages, tools)
    errors = []
    for ch in _channels():
        if model and ch["name"] == "primary":
            ch = dict(ch, model=normalize_model(model))
        body = _payload(ch, messages, tools, temperature, max_tokens, False,
                        json_mode)
        try:
            resp = _post(ch, body, False)
            data = (_parse_anthropic(resp) if ch["protocol"] == "anthropic"
                    else _parse_openai(resp))
            data["channel"] = ch["name"]
            _record_usage(data["usage"])
            return data
        except LLMError as exc:
            errors.append(f"{ch['name']}: {exc.code} {exc.message}")
            continue
        except Exception as exc:
            errors.append(f"{ch['name']}: {type(exc).__name__} {exc}")
            continue
    raise LLMError("internal", "所有模型通道均失败: " + " | ".join(errors)[:800],
                   retryable=True)


def _record_usage(usage):
    try:
        db.bump_usage("__platform__", usage.get("in") or 0,
                      usage.get("out") or 0)
    except Exception:
        pass


def stream(messages, tools=None, model=None, temperature=0.2,
           max_tokens=4000):
    """Yield events: channel, delta, tool_calls, usage, done.

    Falls back to the next channel only when no token has been emitted yet.
    """
    if stub_mode():
        out = _stub(messages, tools)
        yield {"type": "channel", "name": "stub"}
        for i in range(0, len(out["text"]), 24):
            yield {"type": "delta", "text": out["text"][i:i + 24]}
        yield {"type": "usage", **out["usage"]}
        yield {"type": "done"}
        return
    errors = []
    for ch in _channels():
        if model and ch["name"] == "primary":
            ch = dict(ch, model=normalize_model(model))
        body = _payload(ch, messages, tools, temperature, max_tokens, True, False)
        state = {"emitted": False}
        try:
            resp = _post(ch, body, True)
            yield {"type": "channel", "name": ch["name"]}
            if ch["protocol"] == "anthropic":
                text, calls, usage = yield from _stream_anthropic(resp, state)
            else:
                text, calls, usage = yield from _stream_openai(resp, state)
            if calls:
                yield {"type": "tool_calls", "calls": calls}
            yield {"type": "usage", **usage}
            yield {"type": "done"}
            _record_usage(usage)
            return
        except LLMError as exc:
            if not state["emitted"]:
                errors.append(f"{ch['name']}: {exc.code} {exc.message}")
                continue
            raise
        except Exception as exc:
            if not state["emitted"]:
                errors.append(f"{ch['name']}: {type(exc).__name__} {exc}")
                continue
            raise LLMError("internal", f"流式调用中断: {exc}")
    raise LLMError("internal", "所有模型通道均失败: " + " | ".join(errors)[:800],
                   retryable=True)


def _iter_sse(resp):
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("", "[DONE]"):
            continue
        try:
            yield json.loads(payload)
        except Exception:
            continue


def _stream_openai(resp, state):
    text_parts, calls_by_index, usage = [], {}, {"in": 0, "out": 0}
    for data in _iter_sse(resp):
        if data.get("usage"):
            usage = {"in": data["usage"].get("prompt_tokens") or 0,
                     "out": data["usage"].get("completion_tokens") or 0}
        for choice in data.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                state["emitted"] = True
                text_parts.append(piece)
                yield {"type": "delta", "text": piece}
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = calls_by_index.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
    calls = []
    for idx in sorted(calls_by_index):
        slot = calls_by_index[idx]
        try:
            args = json.loads(slot["arguments"] or "{}")
        except Exception:
            args = {"_raw": slot["arguments"]}
        calls.append({"id": slot["id"] or f"call_{idx}", "name": slot["name"],
                      "arguments": args})
    if not usage["out"]:
        usage["out"] = sum(len(p) for p in text_parts) // 3
    return "".join(text_parts), calls, usage


def _stream_anthropic(resp, state):
    text_parts, calls, current = [], [], None
    usage = {"in": 0, "out": 0}
    for data in _iter_sse(resp):
        typ = data.get("type")
        if typ == "message_start":
            usage["in"] = ((data.get("message") or {}).get("usage") or {}).get(
                "input_tokens") or 0
        elif typ == "content_block_start":
            block = data.get("content_block") or {}
            if block.get("type") == "tool_use":
                current = {"id": block.get("id") or "", "name": block.get("name", ""),
                           "arguments": ""}
            else:
                current = None
        elif typ == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                piece = delta.get("text") or ""
                if piece:
                    state["emitted"] = True
                    text_parts.append(piece)
                    yield {"type": "delta", "text": piece}
            elif delta.get("type") == "input_json_delta" and current is not None:
                current["arguments"] += delta.get("partial_json") or ""
        elif typ == "content_block_stop":
            if current is not None:
                try:
                    args = json.loads(current["arguments"] or "{}")
                except Exception:
                    args = {"_raw": current["arguments"]}
                calls.append({"id": current["id"], "name": current["name"],
                              "arguments": args})
                current = None
        elif typ == "message_delta":
            usage["out"] = (data.get("usage") or {}).get("output_tokens") or usage["out"]
    if not usage["out"]:
        usage["out"] = sum(len(p) for p in text_parts) // 3
    return "".join(text_parts), calls, usage


def parse_json(text):
    """Best-effort JSON extraction with fence stripping."""
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


def health():
    chans = _channels()
    return {
        "configured": bool(chans),
        "stub": stub_mode(),
        "channels": [{"name": c["name"], "protocol": c["protocol"],
                      "model": c["model"],
                      "base_url": re.sub(r"//[^/]+@", "//", c["base_url"])}
                     for c in chans],
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }