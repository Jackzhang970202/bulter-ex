# -*- coding: utf-8 -*-
"""Chat attachments: store, extract text, and build untrusted context.

Attachments are per-user, temporary, and never enter the knowledge base
unless the user explicitly ingests them. Text is capped before it reaches a
prompt; the full file stays on disk for the retention window.
"""
import os
import re
import shutil

from .. import config, db, ingest
from . import services

MAX_BYTES = 50 * 1024 * 1024
MAX_CHARS = 30000
ROOT = os.path.join(config.DATA, "platform_uploads")


def _safe_name(name):
    name = os.path.basename(name or "file")
    name = re.sub(r"[\\/:*?\"<>|\r\n]+", "-", name).strip() or "file"
    return name[:120]


def _extract_text(path, kind):
    if kind in ("md", "txt", "csv", "json", "log"):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if kind == "docx":
        return ingest.docx_to_md(path)
    if kind == "pptx":
        return ingest.pptx_to_md(path)
    if kind in ("xlsx", "xls"):
        return ingest.xlsx_to_md(path)
    if kind == "pdf":
        try:
            import pdfplumber
            parts = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages[:60]:
                    parts.append(page.extract_text() or "")
            text = "\n".join(parts).strip()
            if len(text) < 40:
                raise ValueError("PDF 没有可提取文本（可能是扫描件）")
            return text
        except Exception as exc:
            raise ValueError(f"PDF 解析失败：{exc}")
    if kind == "html":
        from . import webingest
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            title, md = webingest.extract(fh.read(), "")
        return f"# {title}\n\n{md}"
    raise ValueError("暂不支持该附件类型；图片/录音请先入库再引用")


def save(user_id, filename, stream, size_limit=MAX_BYTES, extract=True):
    name = _safe_name(filename)
    kind = ingest.kind_of(name)
    if kind in ("unknown", "image", "audio", "video"):
        return None, f"暂不支持 {kind} 附件：{name}"
    aid = services.uid("fa_")
    folder = os.path.join(ROOT, aid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    size = 0
    with open(path, "wb") as out:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > size_limit:
                out.close()
                os.remove(path)
                return None, f"文件超过 {size_limit // (1024 * 1024)}MB：{name}"
            out.write(chunk)
    if extract:
        try:
            text = _extract_text(path, kind)
        except Exception as exc:
            return None, f"{name} 解析失败：{exc}"
    else:
        text = ""
    text = (text or "").strip()
    if extract and not text:
        return None, f"{name} 没有可读文本"
    chars = len(text)
    db.x("INSERT INTO pf_attachments(id,user_id,name,kind,path,chars,text,"
         "created_at) VALUES(?,?,?,?,?,?,?,?)",
         (aid, user_id or "", name, kind, path, chars,
          text[:MAX_CHARS], services.now()))
    return {"id": aid, "name": name, "kind": kind, "bytes": size,
            "chars": chars, "truncated": chars > MAX_CHARS,
            "preview": text[:300]}, ""


def public(row):
    return {"id": row["id"], "name": row["name"], "kind": row["kind"],
            "chars": row["chars"],
            "truncated": (row["chars"] or 0) > MAX_CHARS,
            "created_at": row["created_at"]}


def get(attachment_id, user_id=""):
    row = db.one("SELECT * FROM pf_attachments WHERE id=?", (attachment_id,))
    if not row:
        return None
    if user_id and row.get("user_id") and row["user_id"] != user_id:
        return None
    return row


def context(file_ids, user_id="", max_chars=60000):
    """Return (prompt_block, names) for the selected attachments."""
    blocks, names, used = [], [], 0
    for fid in (file_ids or [])[:10]:
        row = get(fid, user_id)
        if not row:
            continue
        text = (row.get("text") or "")[:MAX_CHARS]
        block = f"### 附件：{row['name']}\n{text}"
        if used + len(block) > max_chars:
            block = block[:max(0, max_chars - used)]
        if not block.strip():
            continue
        blocks.append(block)
        names.append(row["name"])
        used += len(block)
        if used >= max_chars:
            break
    return "\n\n".join(blocks), names


def delete(attachment_id, user_id=""):
    row = get(attachment_id, user_id)
    if not row:
        return False
    path = row.get("path") or ""
    root = os.path.abspath(ROOT)
    if path and os.path.abspath(path).startswith(root + os.sep) \
            and os.path.exists(path):
        try:
            os.remove(path)
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass
    shutil.rmtree(os.path.join(config.DATA, "platform_excel", attachment_id),
                  ignore_errors=True)
    db.x("DELETE FROM pf_attachments WHERE id=?", (attachment_id,))
    return True