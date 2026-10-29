# -*- coding: utf-8 -*-
"""Parser engine registry (W1).

Mirrors the source project's contract: a file type maps to an engine, each
engine turns bytes into ``(markdown, images, metadata, error)``. Engines that
need an external service or binary are only advertised when available.

Builtin engines wrap the existing KnowLens parsers (pypdfium2/PDF,
python-docx/pptx/openpyxl, trafilatura/HTML, OCR, ASR). ``markitdown`` is
optional; LibreOffice/ImageMagick/antiword are detected at runtime.
"""
import csv
import email
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from .. import audio, config, ingest, pdfproc
from ..platform import webingest

DOCUMENT_TYPES = {
    "pdf", "txt", "docx", "doc", "epub", "html", "htm", "mhtml", "md",
    "markdown", "mdx", "csv", "xlsx", "xls", "pptx", "ppt", "json",
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff",
    "mp3", "wav", "m4a", "flac", "ogg",
}
VIDEO_TYPES = {"mp4", "mov", "avi", "mkv", "webm", "wmv", "flv"}
IMAGE_TYPES = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff"}
TEXT_TYPES = {"txt", "md", "markdown", "mdx", "json"}

ENGINE_DESCRIPTIONS = {
    "builtin": "内置解析（pypdfium2 / python-docx / openpyxl / trafilatura）",
    "markitdown": "MarkItDown（Microsoft，多格式转 Markdown）",
    "simple": "纯文本直读（md / txt / csv / json）",
}


class ParserError(Exception):
    pass


@dataclass
class ParsedDocument:
    markdown: str = ""
    images: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    engine: str = ""
    error: str = ""


def kind_of(name):
    return (name or "").rsplit(".", 1)[-1].lower() if "." in name else ""


def normalize_type(value):
    return (value or "").lower().lstrip(".")


def is_supported(name):
    return kind_of(name) in DOCUMENT_TYPES


def is_video(name):
    return kind_of(name) in VIDEO_TYPES


def _has_binary(name):
    return bool(shutil.which(name))


def available_engines():
    engines = {"builtin": {"name": "builtin", "available": True,
                           "description": ENGINE_DESCRIPTIONS["builtin"],
                           "file_types": sorted(DOCUMENT_TYPES)}}
    try:
        import markitdown  # noqa: F401
        engines["markitdown"] = {
            "name": "markitdown", "available": True,
            "description": ENGINE_DESCRIPTIONS["markitdown"],
            "file_types": ["pdf", "docx", "doc", "pptx", "ppt", "xlsx",
                           "xls", "csv", "md", "html", "htm", "txt"]}
    except Exception:
        pass
    engines["simple"] = {"name": "simple", "available": True,
                         "description": ENGINE_DESCRIPTIONS["simple"],
                         "file_types": sorted(TEXT_TYPES | {"csv"})}
    external = {
        "mineru": ("MinerU：版面/表格/公式解析（需配置端点）",
                   ["pdf", "jpg", "jpeg", "png", "bmp", "tiff", "doc",
                    "docx", "ppt", "pptx"],
                   bool(os.environ.get("MINERU_ENDPOINT"))),
        "paddleocr_vl": ("PaddleOCR-VL：OCR 与版面解析（需配置端点）",
                        ["pdf", "jpg", "jpeg", "png", "bmp", "tiff"],
                        bool(os.environ.get("PADDLEOCR_ENDPOINT"))),
        "opendataloader": ("OpenDataLoader：PDF 版面分析（需 Java 11+）",
                          ["pdf"], _has_binary("java")),
        "anydoc": ("anydoc：进程内 Office 解析（未接入）",
                  ["doc", "docx", "odt", "rtf", "ppt", "pptx", "xls",
                   "xlsx", "ods", "epub", "csv", "pdf"], False),
    }
    for name, (description, file_types, ok) in external.items():
        engines[name] = {
            "name": name, "available": bool(ok),
            "description": description, "file_types": file_types,
            "reason": "" if ok else "依赖未安装或未配置",
        }
    return engines


def resolve_engine(file_type, rules=None):
    """Document-level override > KB rules > default engine for the type."""
    file_type = normalize_type(file_type)
    for rule in rules or []:
        types = [normalize_type(t) for t in (rule.get("file_types") or [])]
        if file_type in types and rule.get("engine"):
            return rule["engine"]
    if file_type in ("ppt", "pptx"):
        return "markitdown" if _engine_available("markitdown") else "builtin"
    return "builtin"


def _engine_available(name):
    return name in available_engines()


def parse(data, name, engine=None, overrides=None):
    """Parse bytes into markdown. Never raises; returns ``ParsedDocument``."""
    overrides = overrides or {}
    file_type = kind_of(name)
    engine = engine or resolve_engine(file_type)
    if file_type in VIDEO_TYPES:
        return ParsedDocument(engine=engine, error="暂不支持视频文件")
    if file_type not in DOCUMENT_TYPES:
        return ParsedDocument(engine=engine,
                              error=f"不支持的文件类型：{file_type or '未知'}")
    tmpdir = tempfile.mkdtemp(prefix="kp-parse-")
    path = os.path.join(tmpdir, os.path.basename(name or f"file.{file_type}"))
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        if engine == "markitdown":
            return _parse_markitdown(path, name, engine)
        if engine == "simple":
            return _parse_simple(path, name, file_type, engine)
        return _parse_builtin(path, name, file_type, engine, overrides)
    except Exception as exc:
        return ParsedDocument(engine=engine,
                              error=f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ────────────────────────── builtin ──────────────────────────

def _parse_builtin(path, name, file_type, engine, overrides):
    if file_type == "pdf":
        return _parse_pdf(path, name, engine, overrides)
    if file_type == "docx":
        return ParsedDocument(ingest.docx_to_md(path), engine=engine)
    if file_type == "pptx":
        return ParsedDocument(ingest.pptx_to_md(path), engine=engine)
    if file_type in ("xlsx", "xls"):
        return ParsedDocument(ingest.xlsx_to_md(path), engine=engine)
    if file_type == "doc":
        return _parse_doc(path, engine)
    if file_type == "csv":
        return ParsedDocument(_csv_to_md(path), engine=engine)
    if file_type in TEXT_TYPES:
        return ParsedDocument(_read_text(path), engine=engine)
    if file_type in ("html", "htm"):
        _, markdown = webingest.extract(_read_text(path), "")
        return ParsedDocument(markdown, engine=engine)
    if file_type == "mhtml":
        return _parse_mhtml(path, engine)
    if file_type == "epub":
        return _parse_epub(path, engine)
    if file_type in IMAGE_TYPES:
        return _parse_image(path, name, engine, overrides)
    if file_type in ("mp3", "wav", "m4a", "flac", "ogg"):
        return _parse_audio(path, name, engine)
    raise ParserError(f"内置引擎不支持 {file_type}")


def _parse_pdf(path, name, engine, overrides):
    scanned = bool(overrides.get("pdf_force_scanned"))
    pages, images = pdfproc.extract(path, name, False, None,
                                    scanned=scanned or None)
    parts, page_map, pos = [], [], 0
    for index, page in enumerate(pages or []):
        number = int(page.get("page_index") or index + 1)
        text = (page.get("markdown") or "").strip()
        marker = f"\n\n<!-- page:{number} -->\n\n"
        parts.append(marker + text)
        pos += len(marker) + len(text)
        page_map.append({"page": number, "end": pos})
    metadata = {"pages": len(pages or []), "page_map": page_map}
    return ParsedDocument("".join(parts), images or [], metadata, engine)


def _parse_doc(path, engine):
    antiword = shutil.which("antiword")
    if antiword:
        proc = subprocess.run([antiword, path], capture_output=True,
                              timeout=60)
        if proc.returncode == 0:
            return ParsedDocument(proc.stdout.decode("utf-8", "replace"),
                                  engine=engine)
    raise ParserError("解析 .doc 需要安装 LibreOffice 或 antiword")


def _parse_mhtml(path, engine):
    with open(path, "rb") as fh:
        message = email.message_from_binary_file(fh)
    html = ""
    for part in message.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            html = payload.decode(charset, "replace")
            break
    if not html:
        raise ParserError("MHTML 中没有 HTML 正文")
    _, markdown = webingest.extract(html, "")
    return ParsedDocument(markdown, engine=engine)


def _parse_epub(path, engine):
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup
    try:
        from markdownify import markdownify
    except Exception:
        markdownify = None
    book = epub.read_epub(path)
    parts = []
    for item in book.get_items():
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        soup = BeautifulSoup(item.get_content(), "lxml")
        text = soup.get_text("\n")
        if not text.strip():
            continue
        if markdownify:
            parts.append(markdownify(str(soup), heading_style="ATX"))
        else:
            parts.append(text)
    return ParsedDocument("\n\n".join(parts), engine=engine)


def _parse_image(path, name, engine, overrides):
    with open(path, "rb") as fh:
        data = fh.read()
    markdown = f"![{name}]({name})"
    metadata = {"image": True}
    if overrides.get("enable_ocr", True):
        try:
            ocr = ingest.vision_ocr_image(data)
            if ocr and ocr.strip():
                markdown += "\n\n" + ocr
                metadata["ocr"] = True
        except Exception as exc:
            metadata["ocr_error"] = f"{type(exc).__name__}: {exc}"
    return ParsedDocument(markdown, [], metadata, engine)


def _parse_audio(path, name, engine):
    try:
        markdown = audio.transcribe_to_minutes_md(path, name, None)
    except Exception as exc:
        raise ParserError(f"音频转写失败：{type(exc).__name__}: {exc}")
    return ParsedDocument(markdown, engine=engine)


def _read_text(path):
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            with open(path, "r", encoding=encoding) as fh:
                return fh.read()
        except (UnicodeError, UnicodeDecodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _csv_to_md(path):
    rows = list(csv.reader(io.StringIO(_read_text(path))))[:2000]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    lines = []
    for row in rows:
        row = row + [""] * (width - len(row))
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
        if len(lines) == 1:
            lines.append("|" + "---|" * width)
    return "\n".join(lines)


# ────────────────────────── markitdown / simple ──────────────────────────

def _parse_markitdown(path, name, engine):
    from markitdown import MarkItDown
    result = MarkItDown().convert(path)
    text = getattr(result, "text_content", "") or ""
    if not text.strip():
        raise ParserError("MarkItDown 未产出文本")
    return ParsedDocument(text, engine=engine)


def _parse_simple(path, name, file_type, engine):
    if file_type == "csv":
        return ParsedDocument(_csv_to_md(path), engine=engine)
    return ParsedDocument(_read_text(path), engine=engine)


def preview_markdown(path):
    """Small helper used by tests/tools."""
    return _read_text(path)