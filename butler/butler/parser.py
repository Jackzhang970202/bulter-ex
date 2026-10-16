"""transcript JSONL 防御式解析器。
官方对 ~/.claude 下格式【无稳定性承诺】——一切未知字段/类型静默跳过并计数，
绝不抛异常。compact 会重复追加历史：按消息 uuid 去重。"""
import json
from typing import Optional

# 归一化后的行：{ok, kind, uuid, ts, role, text, usage{in,cache_w,cache_r,out},
#               model, cwd, compact, is_compact_summary}
USAGE_KEYS = {
    "in": ("input_tokens", 0),
    "cache_w": ("cache_creation_input_tokens", 0),
    "cache_r": ("cache_read_input_tokens", 0),
    "out": ("output_tokens", 0),
}


def _blocks_text(content) -> str:
    """从 message.content 提取纯文本（跳过 thinking/tool_use/tool_result）。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                t = (b.get("text") or "").strip()
                if t:
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _usage(message: Optional[dict]) -> dict:
    u = (message or {}).get("usage")
    out = {}
    if isinstance(u, dict):
        for k, (key, default) in USAGE_KEYS.items():
            v = u.get(key)
            out[k] = v if isinstance(v, int) else default
    else:
        out = {k: d for k, (kk, d) in USAGE_KEYS.items()}
    return out


def parse_line(raw: str) -> Optional[dict]:
    """raw: 一行 JSON。返回归一化 dict；无法识别→None（unknown 计数由调用方处理）。"""
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    typ = obj.get("type")
    message = obj.get("message") if isinstance(obj.get("message"), dict) else None
    subtype = obj.get("subtype") or ""
    compact = (typ == "system" and "compact" in str(subtype).lower()) or \
              "compact_boundary" in json.dumps(obj)[:400].lower()

    if typ in ("user", "assistant") and message is not None:
        role = message.get("role") or typ
        text = _blocks_text(message.get("content"))
        uuid = obj.get("uuid") or message.get("id") or ""
        if not uuid:
            uuid = "noid-%d" % hash(raw)          # 兜底去重键
        return {
            "ok": True, "kind": typ, "uuid": str(uuid),
            "ts": obj.get("timestamp") or "",
            "role": role, "text": text,
            "usage": _usage(message),
            "model": message.get("model") or "",
            "cwd": obj.get("cwd") or "",
            "compact": compact,
            "is_compact_summary": bool(obj.get("isCompactSummary")),
            "session_id": obj.get("sessionId") or obj.get("session_id") or "",
        }
    if typ in ("system", "attachment", "progress", "tool"):
        return {"ok": True, "kind": "system", "uuid": str(obj.get("uuid") or f"sys{compact}"),
                "ts": obj.get("timestamp") or "", "role": "", "text": "",
                "usage": _usage(None), "model": "", "cwd": obj.get("cwd") or "",
                "compact": compact, "is_compact_summary": False,
                "session_id": obj.get("sessionId") or obj.get("session_id") or ""}
    if isinstance(typ, str):
        # 已知但不是对话的行（queue-operation/last-prompt/summary/mode 等）：
        # 记为 skip 行——正常消费水位，不算漂移。漂移只统计"连 type 都没有/坏 JSON"。
        return {"ok": True, "kind": "skip", "uuid": str(obj.get("uuid") or f"sk{compact}"),
                "ts": obj.get("timestamp") or "", "role": "", "text": "",
                "usage": _usage(None), "model": "", "cwd": obj.get("cwd") or "",
                "compact": compact, "is_compact_summary": False,
                "session_id": obj.get("sessionId") or obj.get("session_id") or ""}
    return None


def read_increment(path, offset: int, start_line: int = 1):
    """从字节偏移增量读取，只消费完整行（\n 结尾）。半行留到下次。
    返回 (entries, new_offset, unknown_count, unknown_samples)。
    entry 附带 line_no / byte_off（该行结尾），供证据回放与滚存截断。"""
    entries, unknown, samples = [], 0, []
    new_off = offset
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return entries, offset, 0, []
    last_nl = chunk.rfind(b"\n")
    if last_nl == -1:
        return entries, offset, 0, []               # 没有完整行
    complete = chunk[:last_nl + 1]
    new_off = offset + len(complete)
    pos = offset
    for raw_line in complete.decode("utf-8", errors="replace").splitlines(keepends=True):
        pos += len(raw_line.encode("utf-8", errors="replace"))   # CRLF 字节级兼容
        stripped = raw_line.strip()
        if not stripped:
            start_line += 1
            continue
        d = parse_line(stripped)
        if d is None:
            unknown += 1
            if len(samples) < 5:
                samples.append(stripped[:400])
            start_line += 1
            continue
        d["line_no"] = start_line
        d["byte_off"] = pos
        entries.append(d)
        start_line += 1
    return entries, new_off, unknown, samples


def total_usage_bytes(path) -> int:
    import os
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
