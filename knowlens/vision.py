# -*- coding: utf-8 -*-
"""Vision understanding: image OCR + semantic description of charts/diagrams.

- Uses the current model channel (mimo has been verified to support image input)
- Cached by image SHA-256 to avoid paying twice when rebuilding an index
- Small images are upscaled, large images are limited by their longest side; decorative images are filtered by the caller
"""
import base64
import hashlib
import io
import json
import os
import time

from . import config, db, llm

PROMPT_VERSION = "v2"
_cache_dir = None
_img_dir = None


def _dirs():
    global _cache_dir, _img_dir
    if _cache_dir is None:
        _cache_dir = config.OCR_CACHE_DIR
        _img_dir = os.path.join(_cache_dir, "img")
        os.makedirs(_img_dir, exist_ok=True)
    return _cache_dir, _img_dir


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_path(sha: str, kind: str) -> str:
    cdir, _ = _dirs()
    return os.path.join(cdir, f"{sha}-{kind}-{PROMPT_VERSION}.json")


def _read_cache(sha: str, kind: str):
    p = _cache_path(sha, kind)
    if os.path.isfile(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None


def _write_cache(sha: str, kind: str, obj: dict):
    p = _cache_path(sha, kind)
    try:
        json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _prepare(png_bytes: bytes) -> tuple[bytes, tuple]:
    """PNG preprocessing: normalize to RGB, upscale small images so the short side is 400, and cap the longest side of large images."""
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = im.size
    if min(w, h) < 400:
        k = 400 / max(1, min(w, h))
        im = im.resize((int(w * k), int(h * k)), Image.LANCZOS)
    w, h = im.size
    if max(w, h) > config.VISION_MAX_SIDE:
        k = config.VISION_MAX_SIDE / max(w, h)
        im = im.resize((int(w * k), int(h * k)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), im.size


def _save_image(png_bytes: bytes, sha: str) -> str:
    _, idir = _dirs()
    p = os.path.join(idir, f"{sha}.png")
    if not os.path.isfile(p):
        with open(p, "wb") as f:
            f.write(png_bytes)
    return p


def _sanitize_json(t: str) -> str:
    """Repair model JSON: unescaped newlines/tabs inside strings, and trailing commas."""
    import re as _re
    out, in_str, esc = [], False, False
    for ch in t:
        if esc:
            out.append(ch); esc = False; continue
        if ch == "\\" and in_str:
            out.append(ch); esc = True; continue
        if ch == '"':
            in_str = not in_str; out.append(ch); continue
        if in_str and ch in "\n\r\t":
            out.append({" \n": "\\n", "\n": "\\n", "\r": "\\r",
                        "\t": "\\t"}[ch])
            continue
        out.append(ch)
    s = "".join(out)
    s = _re.sub(r",\s*([}\]])", r"\1", s)
    return s


def _extract_json(text: str):
    t = text.strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    body = t[i:j + 1]
    for candidate in (body, _sanitize_json(body)):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def _extract_fields(text: str) -> dict:
    """Fallback field recovery by regex when JSON parsing fails completely."""
    import re as _re
    obj = {}
    for name in ("type", "transcription", "description"):
        m = _re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % name, text, _re.S)
        if m:
            try:
                obj[name] = json.loads('"' + m.group(1) + '"')
            except Exception:
                obj[name] = m.group(1)
    return obj


def _call_vision(png_bytes: bytes, prompt: str, max_tokens: int = 3000,
                 retries: int = 2) -> tuple[str, bytes]:
    import litellm
    prepared, _ = _prepare(png_bytes)
    b64 = base64.b64encode(prepared).decode()
    last = None
    for attempt in range(retries + 1):
        try:
            r = litellm.completion(
                model=llm.model_ref(), timeout=180, drop_params=True,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": "data:image/png;base64," + b64}}]}])
            try:
                u = getattr(r, "usage", None)
                db.bump_usage("__global__",
                              getattr(u, "prompt_tokens", 0) or 0,
                              getattr(u, "completion_tokens", 0) or 0)
            except Exception:
                pass
            return (r.choices[0].message.content or "").strip(), prepared
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"视觉调用失败：{type(last).__name__}: {str(last)[:200]}")


IMAGE_PROMPT = (
    "你是文档图片理解助手。图片来自《{doc}》{pos}。\n"
    "上文片段：「{prev}」\n下文片段：「{next}」\n"
    "请完成：\n"
    "1) 逐字转写图中全部文字；表格转为 markdown 表格；\n"
    "2) 无论是否含文字，都必须详细理解图片本身：用 2–6 句中文说明"
    "它是什么类型的图、展示了什么对象、结构/趋势/关键数值、与上下文的关系；"
    "流程图要说明步骤与走向，界面截图要说明功能区域，照片要说明场景；\n"
    "3) 判断类型：chart（图表）/ table（表格）/ diagram（流程图或示意图）/ "
    "ui（界面截图）/ photo（照片）/ scan（扫描页）/ other。\n"
    "输出要求：只输出一个 JSON 对象，不要代码块、不要解释；"
    "字符串内的换行必须写成 \\n。\n"
    '格式：{{"type":"...","transcription":"...","description":"..."}}'
)


def identify_image(png_bytes: bytes, doc_name: str = "", page: int = 0,
                   prev_text: str = "", next_text: str = "",
                   hint: str = "", force: bool = False) -> dict:
    """Understand a single image. On failure, returns a record with status=failed instead of raising.

    force=True skips the cache and overwrites it (used by the UI's "re-identify" action).
    """
    sha = sha256(png_bytes)
    if not force:
        cached = _read_cache(sha, "image")
        if cached:
            cached = dict(cached)
            cached["img_path"] = _save_image(png_bytes, sha)
            return cached
    rec = {"type": "other", "transcription": "", "description": ""}
    try:
        pos = f"第{page}页" if page else "（文档内图片）"
        prompt = IMAGE_PROMPT.format(
            doc=doc_name or "未命名文档", pos=pos,
            prev=(prev_text or "（无）")[:600],
            next=(next_text or "（无）")[:600])
        text, _ = _call_vision(png_bytes, prompt, max_tokens=4000)
        obj = _extract_json(text) or _extract_fields(text)
        if obj:
            rec["type"] = str(obj.get("type") or "other")[:20]
            rec["transcription"] = str(obj.get("transcription") or "")[:6000]
            rec["description"] = str(obj.get("description") or "")[:3000]
        if not (rec["transcription"] or rec["description"]):
            rec["description"] = text[:2000]
        rec["status"] = "ok"
    except Exception as e:
        rec["status"] = "failed"
        rec["description"] = f"图片识别失败：{str(e)[:160]}"
    rec["sha"] = sha
    rec["img_path"] = _save_image(png_bytes, sha)
    rec["hint"] = hint
    _write_cache(sha, "image", rec)
    return rec


FULLPAGE_PROMPT = (
    "这是《{doc}》第 {page} 页的扫描图。请完整转写为 markdown，"
    "保留标题层级（#/##）、表格用 | 语法、列表；图表用一句话说明其内容。"
    "只输出 markdown，不要任何解释。"
)


def full_page_ocr(png_bytes: bytes, doc_name: str, page: int) -> dict:
    """Full-page OCR for scanned pages; returns {markdown, status}."""
    sha = sha256(png_bytes) + f"-p{page}"
    cached = _read_cache(sha, "page")
    if cached:
        return cached
    try:
        text, prepared = _call_vision(
            png_bytes, FULLPAGE_PROMPT.format(doc=doc_name, page=page),
            max_tokens=4000)
        rec = {"markdown": text or f"（第 {page} 页未识别出内容）",
               "status": "ok", "sha": sha}
    except Exception as e:
        rec = {"markdown": f"（第 {page} 页图片识别失败：{str(e)[:120]}）",
               "status": "failed", "sha": sha}
    _write_cache(sha, "page", rec)
    return rec


def image_markdown(rec: dict, page: int = 0, seq: int = 0) -> str:
    pos = []
    if page:
        pos.append(f"第{page}页")
    if seq:
        pos.append(f"图{seq}")
    head = " · ".join(pos) or "文档图片"
    lines = [f"【图片理解 · {head}（{rec.get('type') or 'other'}）】"]
    if rec.get("transcription"):
        lines.append("转写：" + rec["transcription"].replace("\n", "\n"))
    if rec.get("description"):
        lines.append("说明：" + rec["description"])
    if rec.get("status") == "failed":
        lines.append("（本图识别失败，仅保留占位）")
    return "\n".join(lines)