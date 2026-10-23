# -*- coding: utf-8 -*-
"""PDF layout analysis: reading order of text/image blocks, full-page OCR for scanned documents, and tree building by original page.

Backends:
- Default: pdfplumber (MIT) + pypdfium2 (Apache/BSD) — for open-source distribution;
- If PyMuPDF (AGPL) is installed and pdfplumber is not explicitly forced, use its faster layout analysis.
Selection: environment variable KNOWLENS_PDF_BACKEND=pdfplumber|fitz.
"""
import io
import os

from . import config, vision

SCALE = config.PDF_RENDER_SCALE


def _use_fitz() -> bool:
    if os.environ.get("KNOWLENS_PDF_BACKEND", "").lower() == "pdfplumber":
        return False
    try:
        import fitz  # noqa: F401
        return True
    except Exception:
        return False


def _scanned(text_lengths):
    n = max(1, len(text_lengths))
    avg = sum(text_lengths) / n
    low = sum(1 for c in text_lengths if c < 20)
    return avg < config.SCAN_TEXT_THRESHOLD or low >= max(1, n * 0.6)


def _order_blocks(blocks, width):
    """Reading order: sort single-column by y/x; for suspected two-column layouts, process the left column before the right."""
    if not blocks:
        return []
    left = [b for b in blocks if (b[0] + b[2]) / 2 < width * 0.45]
    right = [b for b in blocks if (b[0] + b[2]) / 2 >= width * 0.55]
    mid = [b for b in blocks if b not in left and b not in right]
    key = lambda b: (round(b[1], 1), b[0])  # noqa: E731
    if len(left) >= 2 and len(right) >= 2 and len(mid) <= len(blocks) * 0.3:
        return sorted(left, key=key) + sorted(mid, key=key) + sorted(right, key=key)
    return sorted(blocks, key=key)


def _nearest_text(ordered, i, step):
    j = i + step
    while 0 <= j < len(ordered):
        b = ordered[j]
        if b[6] == 0 and b[4].strip():
            return b[4].strip()
        j += step
    return ""


# ────────────────────────── analyze ──────────────────────────

def _analyze_fitz(path):
    import fitz
    doc = fitz.open(path)
    texts, has_images = [], False
    for page in doc:
        texts.append(len((page.get_text("text") or "").strip()))
        if not has_images and _fitz_image_blocks(page):
            has_images = True
    doc.close()
    return {"pages": len(texts), "scanned": _scanned(texts),
            "has_images": has_images, "avg_chars": sum(texts) / max(1, len(texts))}


def _analyze_plumber(path):
    import pdfplumber
    texts, has_images = [], False
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            texts.append(len((page.extract_text() or "").strip()))
            if page.images:
                has_images = True
    return {"pages": len(texts), "scanned": _scanned(texts),
            "has_images": has_images, "avg_chars": sum(texts) / max(1, len(texts))}


def analyze(path: str) -> dict:
    return _analyze_fitz(path) if _use_fitz() else _analyze_plumber(path)


# ────────────────────────── fitz backend ──────────────────────────

def _fitz_image_blocks(page):
    import fitz
    out = []
    try:
        for info in page.get_images(full=True):
            for rect in page.get_image_rects(info[0]):
                if rect.width > 0 and rect.height > 0:
                    out.append((rect.x0, rect.y0, rect.x1, rect.y1, "", 0, 1))
    except Exception:
        pass
    return out


def _extract_fitz(path, doc_name, vision_on, progress, scanned):
    import fitz
    doc = fitz.open(path)
    total = doc.page_count
    pages, images, seq = [], [], 0
    for pno, page in enumerate(doc, 1):
        if scanned:
            pix = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
            rec = vision.full_page_ocr(pix.tobytes("png"), doc_name, pno)
            pages.append({"page_index": pno, "markdown": rec["markdown"],
                          "ocr": rec.get("status", "ok")})
            if progress:
                progress("ocr", f"整页识别 {pno}/{total} 页")
            continue
        blocks = [b for b in page.get_text("blocks")
                  if len(b) >= 7 and b[6] == 0] + _fitz_image_blocks(page)
        ordered = _order_blocks(blocks, page.rect.width)
        area = page.rect.width * page.rect.height
        parts = []
        for i, b in enumerate(ordered):
            if b[6] == 0:
                t = b[4].strip()
                if t:
                    parts.append(t)
                continue
            rect = fitz.Rect(b[0], b[1], b[2], b[3])
            if (rect.width < config.VISION_MIN_SIDE
                    or rect.height < config.VISION_MIN_SIDE):
                continue
            if rect.width * rect.height < area * config.VISION_MIN_AREA_RATIO:
                continue
            page_text_len = sum(len(b[4]) for b in ordered if b[6] == 0)
            if (rect.width * rect.height > area * 0.85
                    and page_text_len > 200):
                continue
            if not vision_on:
                continue
            seq += 1
            rec = vision.identify_image(
                page.get_pixmap(clip=rect, matrix=fitz.Matrix(SCALE, SCALE)).tobytes("png"),
                doc_name, pno,
                prev_text=_nearest_text(ordered, i, -1),
                next_text=_nearest_text(ordered, i, 1), hint="pdf")
            parts.append(vision.image_markdown(rec, page=pno, seq=seq))
            images.append(_image_record(rec, pno, seq))
            if progress:
                progress("ocr", f"图片理解 第{pno}页 第{seq}张")
        pages.append({"page_index": pno, "markdown": "\n\n".join(parts)})
    doc.close()
    return pages, images


# ────────────────────────── pdfplumber backend ──────────────────────────

def _render_pdfium(doc, index, crop=None):
    kw = {"scale": SCALE}
    page = doc[index]
    if crop:
        try:
            w, h = page.get_size()
            # crop is the amount cropped from each side (left, bottom, right, top), not an absolute rectangle
            l, b, r, t = crop
            l = max(0.0, min(l, w - 1))
            r = max(0.0, min(r, w - 1))
            b = max(0.0, min(b, h - 1))
            t = max(0.0, min(t, h - 1))
            if l + r >= w or b + t >= h:
                return None
            kw["crop"] = (l, b, r, t)
        except Exception:
            return None
    bitmap = page.render(**kw)
    buf = io.BytesIO()
    bitmap.to_pil().save(buf, format="PNG")
    return buf.getvalue()


def _extract_plumber(path, doc_name, vision_on, progress, scanned):
    import pdfplumber
    import pypdfium2 as pdfium
    pdfium_doc = pdfium.PdfDocument(path)
    pages, images, seq = [], [], 0
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for pno, page in enumerate(pdf.pages, 1):
            if scanned:
                png = _render_pdfium(pdfium_doc, pno - 1)
                rec = vision.full_page_ocr(png, doc_name, pno)
                pages.append({"page_index": pno, "markdown": rec["markdown"],
                              "ocr": rec.get("status", "ok")})
                if progress:
                    progress("ocr", f"整页识别 {pno}/{total} 页")
                continue
            blocks = []
            for ln in page.extract_text_lines():
                blocks.append((ln["x0"], ln["top"], ln["x1"], ln["bottom"],
                               ln.get("text") or "", 0, 0))
            for im in page.images:
                blocks.append((im["x0"], im["top"], im["x1"], im["bottom"],
                               "", 0, 1))
            ordered = _order_blocks(blocks, page.width)
            area = page.width * page.height
            page_text_len = sum(len(b[4]) for b in ordered if b[6] == 0)
            parts = []
            for i, b in enumerate(ordered):
                if b[6] == 0:
                    t = b[4].strip()
                    if t:
                        parts.append(t)
                    continue
                w, h = b[2] - b[0], b[3] - b[1]
                if w < config.VISION_MIN_SIDE or h < config.VISION_MIN_SIDE:
                    continue
                if w * h < area * config.VISION_MIN_AREA_RATIO:
                    continue
                if w * h > area * 0.85 and page_text_len > 200:
                    continue
                if not vision_on:
                    continue
                crop = (b[0], page.height - b[3], page.width - b[2], b[1])
                png = _render_pdfium(pdfium_doc, pno - 1, crop)
                if not png:
                    continue
                seq += 1
                rec = vision.identify_image(
                    png, doc_name, pno,
                    prev_text=_nearest_text(ordered, i, -1),
                    next_text=_nearest_text(ordered, i, 1), hint="pdf")
                parts.append(vision.image_markdown(rec, page=pno, seq=seq))
                images.append(_image_record(rec, pno, seq))
                if progress:
                    progress("ocr", f"图片理解 第{pno}页 第{seq}张")
            pages.append({"page_index": pno, "markdown": "\n\n".join(parts)})
    try:
        pdfium_doc.close()
    except Exception:
        pass
    return pages, images


def _image_record(rec, page, seq):
    return {"page": page, "order_no": seq, "img_type": rec.get("type", ""),
            "transcription": rec.get("transcription", ""),
            "description": rec.get("description", ""),
            "status": rec.get("status", "ok"),
            "sha": rec.get("sha", ""), "img_path": rec.get("img_path", "")}


def extract(path: str, doc_name: str, vision_on: bool,
            progress=None, scanned: bool = None):
    """Extract page by page → (pages, images). Full-page OCR for scanned documents; each page maps to its original page number."""
    if _use_fitz():
        return _extract_fitz(path, doc_name, vision_on, progress, scanned)
    return _extract_plumber(path, doc_name, vision_on, progress, scanned)


def build_tree(pages: list, group: int = None):
    """Build a tree grouped by original page: one parent node per group pages, with the pages as child nodes."""
    from pageindex.utils import write_node_id
    group = group or config.PDF_PAGE_GROUP
    nodes = []
    for i in range(0, len(pages), group):
        chunk = pages[i:i + group]
        kids = []
        for p in chunk:
            md = p.get("markdown", "")
            title = ""
            for line in md.splitlines():
                s = line.strip()
                if s.startswith("#"):
                    title = s.lstrip("#").strip()[:60]
                    break
            if not title:
                snippet = next((l.strip() for l in md.splitlines() if l.strip()), "")
                title = (snippet[:30] + ("…" if len(snippet) > 30 else "")) \
                    or f"第 {p['page_index']} 页"
            kids.append({"title": title,
                         "start_index": p["page_index"],
                         "end_index": p["page_index"],
                         "summary": md.strip()[:150]})
        nodes.append({
            "title": f"第 {chunk[0]['page_index']}–{chunk[-1]['page_index']} 页",
            "start_index": chunk[0]["page_index"],
            "end_index": chunk[-1]["page_index"],
            "summary": chunk[0].get("markdown", "").strip()[:150],
            "nodes": kids})
    write_node_id(nodes)
    return nodes