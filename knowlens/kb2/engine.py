# -*- coding: utf-8 -*-
"""Chunk-level KB engine: parse → chunk → FTS5 → retrieve."""
import csv
import hashlib
import io
import json
import os
import re
import threading
import time

from .. import config, db, ingest, pdfproc
from ..platform import webingest
from . import schema

ROOT = os.path.join(config.DATA, "kb2")
_lock = threading.Semaphore(2)


def init():
    schema.init()


def _safe(s):
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", s)[:80]


def create_base(name, description="", chunk_size=512, chunk_overlap=80):
    bid = "kb2_" + os.urandom(6).hex()
    now = db.now()
    db.x("INSERT INTO kb2_bases(id,name,description,chunk_size,chunk_overlap,"
         "status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
         (bid, name.strip(), description.strip(), int(chunk_size),
          int(chunk_overlap), "ready", now, now))
    return db.one("SELECT * FROM kb2_bases WHERE id=?", (bid,))


def list_bases():
    return db.q("SELECT b.*, (SELECT COUNT(*) FROM kb2_knowledges k "
                "WHERE k.kb_id=b.id) docs, "
                "(SELECT COUNT(*) FROM kb2_chunks c WHERE c.kb_id=b.id "
                "AND c.enabled=1) chunks "
                "FROM kb2_bases b ORDER BY b.created_at DESC")


def delete_base(bid):
    for doc in db.q("SELECT id FROM kb2_knowledges WHERE kb_id=?", (bid,)):
        _delete_chunks(doc["id"])
    db.x("DELETE FROM kb2_knowledges WHERE kb_id=?", (bid,))
    db.x("DELETE FROM kb2_bases WHERE id=?", (bid,))
    import shutil
    shutil.rmtree(os.path.join(ROOT, bid), ignore_errors=True)


def list_docs(bid):
    rows = db.q("SELECT * FROM kb2_knowledges WHERE kb_id=? "
                "ORDER BY created_at DESC", (bid,))
    for row in rows:
        row["tags"] = _load_tags(row.get("tags"))
        row["enabled"] = bool(row.get("enabled", 1))
    return rows


def _load_tags(value):
    if isinstance(value, list):
        return value
    try:
        data = json.loads(value or "[]")
    except Exception:
        return []
    return data if isinstance(data, list) else []


def update_base(bid, *, name=None, description=None, chunk_size=None,
                chunk_overlap=None):
    base = db.one("SELECT * FROM kb2_bases WHERE id=?", (bid,))
    if not base:
        return None
    sets, args = [], []
    for key, value in (("name", name), ("description", description)):
        if value is not None:
            sets.append(f"{key}=?")
            args.append(str(value).strip())
    for key, value in (("chunk_size", chunk_size),
                       ("chunk_overlap", chunk_overlap)):
        if value is not None:
            sets.append(f"{key}=?")
            args.append(max(32, int(value)))
    if not sets:
        return base
    args.extend([db.now(), bid])
    db.x(f"UPDATE kb2_bases SET {', '.join(sets)},updated_at=? WHERE id=?",
         args)
    return db.one("SELECT * FROM kb2_bases WHERE id=?", (bid,))


def update_doc(kid, *, tags=None, enabled=None, valid_from=None,
               valid_to=None):
    doc = db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,))
    if not doc:
        return None
    sets, args = [], []
    for key, value in (("valid_from", valid_from), ("valid_to", valid_to)):
        if value is not None:
            sets.append(f"{key}=?")
            args.append(str(value).strip()[:10])
    if tags is not None:
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,，;；]", tags) if t.strip()]
        sets.append("tags=?")
        args.append(json.dumps(tags, ensure_ascii=False))
    if enabled is not None:
        sets.append("enabled=?")
        args.append(1 if enabled else 0)
        # Disabled documents must not surface in retrieval or stats.
        db.x("UPDATE kb2_chunks SET enabled=? WHERE knowledge_id=?",
             (1 if enabled else 0, kid))
    if not sets:
        return doc
    args.extend([db.now(), kid])
    db.x(f"UPDATE kb2_knowledges SET {', '.join(sets)},updated_at=? WHERE id=?",
         args)
    row = db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,))
    row["tags"] = _load_tags(row.get("tags"))
    row["enabled"] = bool(row.get("enabled", 1))
    return row


def set_chunk_enabled(cid, enabled):
    row = db.one("SELECT * FROM kb2_chunks WHERE id=?", (cid,))
    if not row:
        return None
    db.x("UPDATE kb2_chunks SET enabled=? WHERE id=?",
         (1 if enabled else 0, cid))
    return {"id": cid, "enabled": bool(enabled)}


def reindex_doc(kid):
    """Re-parse a document with the current base settings."""
    doc = db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,))
    if not doc:
        return None
    db.x("UPDATE kb2_knowledges SET status='pending',error='',updated_at=? "
         "WHERE id=?", (db.now(), kid))
    threading.Thread(target=_process, args=(kid,), daemon=True).start()
    return db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,))


def _versions_dir(kid):
    folder = os.path.join(ROOT, "_versions", kid)
    os.makedirs(folder, exist_ok=True)
    return folder


def _snapshot(doc):
    """Keep the current file as a restorable version before replacing it."""
    src = doc.get("storage_path") or ""
    if not src or not os.path.exists(src):
        return
    folder = _versions_dir(doc["id"])
    name = f"v{int(doc.get('version') or 1)}-{os.path.basename(src)}"
    target = os.path.join(folder, name)
    try:
        with open(src, "rb") as fh, open(target, "wb") as out:
            out.write(fh.read())
    except OSError:
        return
    db.x("INSERT INTO kb2_doc_versions(id,knowledge_id,version,sha,"
         "storage_path,chars,chunks_count,created_at) "
         "VALUES(?,?,?,?,?,?,?,?)",
         ("kv_" + os.urandom(6).hex(), doc["id"],
          int(doc.get("version") or 1), doc.get("sha") or "", target,
          int(doc.get("chars") or 0), int(doc.get("chunks_count") or 0),
          db.now()))


def list_versions(kid):
    return db.q("SELECT * FROM kb2_doc_versions WHERE knowledge_id=? "
                "ORDER BY version DESC, created_at DESC", (kid,))


def add_version(kid, filename, data, user_id=""):
    """Upload a new revision of an existing document."""
    doc = db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,))
    if not doc:
        return None, "文档不存在"
    sha = hashlib.sha256(data).hexdigest()
    if sha == (doc.get("sha") or ""):
        return None, "与新版本内容相同，无需更新"
    _snapshot(doc)
    path = doc.get("storage_path") or ""
    if not path:
        folder = os.path.join(ROOT, doc["kb_id"], kid)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, os.path.basename(filename or "upload"))
    with open(path, "wb") as fh:
        fh.write(data)
    db.x("UPDATE kb2_knowledges SET storage_path=?,sha=?,version=version+1,"
         "status='pending',error='',updated_at=? WHERE id=?",
         (path, sha, db.now(), kid))
    threading.Thread(target=_process, args=(kid,), daemon=True).start()
    return db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,)), ""


def rollback(kid, version):
    row = db.one("SELECT * FROM kb2_doc_versions WHERE knowledge_id=? AND "
                 "version=?", (kid, int(version)))
    if not row or not os.path.exists(row["storage_path"]):
        return None, "该版本不存在或文件已丢失"
    doc = db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,))
    if not doc:
        return None, "文档不存在"
    _snapshot(doc)
    with open(row["storage_path"], "rb") as src, \
            open(doc["storage_path"], "wb") as out:
        out.write(src.read())
    db.x("UPDATE kb2_knowledges SET sha=?,version=version+1,status='pending',"
         "error='',updated_at=? WHERE id=?",
         (row.get("sha") or "", db.now(), kid))
    threading.Thread(target=_process, args=(kid,), daemon=True).start()
    return db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,)), ""


def _delete_chunks(kid):
    rows = db.q("SELECT rid FROM kb2_chunks WHERE knowledge_id=?", (kid,))
    for row in rows:
        db.x("DELETE FROM kb2_chunks_fts WHERE rowid=?", (row["rid"],))
    db.x("DELETE FROM kb2_chunks WHERE knowledge_id=?", (kid,))


def delete_doc(kid):
    doc = db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,))
    if not doc:
        return False
    _delete_chunks(kid)
    db.x("DELETE FROM kb2_knowledges WHERE id=?", (kid,))
    if doc.get("storage_path"):
        try:
            os.remove(doc["storage_path"])
        except OSError:
            pass
    return True


# ────────────────────────── parsing ──────────────────────────

def _pdf_markdown(path, name):
    pages, _ = pdfproc.extract(path, name, False, None, None)
    parts, page_map = [], []
    pos = 0
    for page in pages or []:
        text = (page.get("markdown") or "").strip()
        marker = f"\n\n<!-- page:{page.get('page_index') or 0} -->\n\n"
        parts.append(marker + text)
        pos += len(marker) + len(text)
        page_map.append((pos, page.get("page_index") or 0))
    return "".join(parts), page_map


def _read_text(path):
    for enc in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            with open(path, "r", encoding=enc) as fh:
                return fh.read()
        except (UnicodeError, UnicodeDecodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _csv_markdown(path):
    rows = list(csv.reader(io.StringIO(_read_text(path))))[:2000]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    lines = []
    for r in rows:
        r = r + [""] * (width - len(r))
        lines.append("| " + " | ".join(x.replace("|", "\\|") for x in r) + " |")
        if len(lines) == 1:
            lines.append("|" + "---|" * width)
    return "\n".join(lines)


def _ext(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def parse_file(path, name):
    """Parse any supported file into (markdown, page_map)."""
    ext = _ext(name)
    if ext in ("csv", "tsv"):
        return _csv_markdown(path), []
    if ext in ("html", "htm"):
        return webingest.extract(_read_text(path), "")[1], []
    if ext in ("json", "log", "yaml", "yml", "ini", "conf", "py", "js",
               "ts", "sql", "xml"):
        return _read_text(path), []
    kind = ingest.kind_of(name)
    if kind == "pdf":
        return _pdf_markdown(path, name)
    if kind == "docx":
        return ingest.docx_to_md(path), []
    if kind == "pptx":
        return ingest.pptx_to_md(path), []
    if kind in ("xlsx", "xls"):
        return ingest.xlsx_to_md(path), []
    if kind == "image":
        with open(path, "rb") as fh:
            return ingest.vision_ocr_image(fh.read()), []
    if kind in ("md", "txt"):
        return _read_text(path), []
    if kind == "audio":
        raise ValueError("音频请使用经典知识空间（带时间码转写）")
    raise ValueError(f"暂不支持的文件类型: {ext or kind}")


# ────────────────────────── chunking ──────────────────────────

def _page_at(offset, page_map):
    page = 0
    for end, number in page_map:
        if offset <= end:
            return number
    return page_map[-1][1] if page_map else 0


def chunk_markdown(md, size=512, overlap=80, page_map=None):
    """Heading-aware chunking with sentence splitting and overlap.

    Offsets stay aligned with the source markdown so ``page_map`` (built from
    ``<!-- page:n -->`` markers) can map every chunk back to its page.
    """
    md = (md or "").replace("\r\n", "\n")
    page_map = page_map or []
    size = max(int(size or 512), 32)
    overlap = max(0, min(int(overlap or 0), size // 2))
    parts = re.split(r"(\n\s*\n)", md)
    heading = ""
    chunks, buf, buf_start, offset = [], "", 0, 0
    sentence_re = re.compile(r"(?<=[。！？!?；;\n])")

    def emit(content, start, end):
        content = content.strip()
        if content:
            chunks.append({"content": content, "heading": heading,
                           "start": start, "end": end,
                           "page": _page_at(start, page_map)})

    def flush(end):
        nonlocal buf
        emit(buf, buf_start, end)
        buf = ""

    for i in range(0, len(parts), 2):
        block = parts[i]
        block_start = offset
        offset += len(block) + (len(parts[i + 1]) if i + 1 < len(parts) else 0)
        if not block.strip():
            continue
        if re.match(r"^\s*<!--\s*page:\d+\s*-->\s*$", block):
            # A page boundary closes the buffer so every chunk cites one page.
            flush(block_start)
            continue
        lines = block.split("\n")
        head_len = 0
        if re.match(r"^#{1,6}\s", lines[0]):
            flush(block_start)
            heading = lines[0].lstrip("#").strip()[:120]
            head_len = len(lines[0]) + 1
            block = "\n".join(lines[1:])
            if not block.strip():
                continue
        if len(block) <= size:
            pieces = [(block, block_start + head_len)]
        else:
            pieces, pos = [], 0
            for sentence in sentence_re.split(block):
                if sentence:
                    pieces.append((sentence, block_start + head_len + pos))
                    pos += len(sentence)
        for piece, piece_start in pieces:
            piece = piece.strip()
            if not piece:
                continue
            if len(piece) > size:
                step = max(size - overlap, 1)
                for j in range(0, len(piece), step):
                    emit(piece[j:j + size], piece_start + j,
                         piece_start + min(j + size, len(piece)))
                continue
            if len(buf) + len(piece) + 1 > size and buf.strip():
                flush(piece_start)
                if overlap:
                    buf = buf[-overlap:]
                    buf_start = piece_start - len(buf)
                else:
                    buf = ""
            if not buf:
                buf_start = piece_start
            buf += ("\n" if buf else "") + piece
    flush(offset)
    return chunks


# ────────────────────────── ingestion ──────────────────────────

def add_file(bid, filename, data, user_id="", tags=None):
    base = db.one("SELECT * FROM kb2_bases WHERE id=?", (bid,))
    if not base:
        return None, "知识库不存在"
    sha = hashlib.sha256(data).hexdigest()
    same = db.one("SELECT id,title FROM kb2_knowledges WHERE kb_id=? AND sha=?",
                  (bid, sha))
    if same:
        return None, f"内容相同，已存在：{same['title']}"
    kid = "kn2_" + os.urandom(6).hex()
    folder = os.path.join(ROOT, bid, kid)
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(filename or "upload")
    path = os.path.join(folder, name)
    with open(path, "wb") as fh:
        fh.write(data)
    now = db.now()
    db.x("INSERT INTO kb2_knowledges(id,kb_id,title,kind,source,storage_path,"
         "status,sha,tags,enabled,created_at,updated_at) "
         "VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
         (kid, bid, name, "file", name, path, "pending", sha,
          json.dumps(tags or [], ensure_ascii=False), now, now))
    threading.Thread(target=_process, args=(kid,), daemon=True).start()
    return db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,)), ""


def _process(kid):
    with _lock:
        doc = db.one("SELECT * FROM kb2_knowledges WHERE id=?", (kid,))
        if not doc:
            return
        db.x("UPDATE kb2_knowledges SET status='processing',updated_at=? "
             "WHERE id=?", (db.now(), kid))
        try:
            md, page_map = parse_file(doc["storage_path"], doc["title"])
            base = db.one("SELECT * FROM kb2_bases WHERE id=?", (doc["kb_id"],))
            chunks = chunk_markdown(
                md, int(base.get("chunk_size") or 512),
                int(base.get("chunk_overlap") or 80), page_map)
            _delete_chunks(kid)
            doc_enabled = 1 if doc.get("enabled", 1) else 0
            for i, ch in enumerate(chunks):
                cid = "ch2_" + os.urandom(6).hex()
                rid = db.x(
                    "INSERT INTO kb2_chunks(id,kb_id,knowledge_id,idx,content,"
                    "heading,page,start_at,end_at,enabled,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, doc["kb_id"], kid, i, ch["content"], ch["heading"],
                     ch["page"], ch["start"], ch["end"], doc_enabled,
                     db.now()))
                db.x("INSERT INTO kb2_chunks_fts(rowid,content,heading) "
                     "VALUES(?,?,?)", (rid, ch["content"], ch["heading"]))
            db.x("UPDATE kb2_knowledges SET status='ready',chars=?,pages=?,"
                 "chunks_count=?,error='',updated_at=? WHERE id=?",
                 (len(md), len(page_map), len(chunks), db.now(), kid))
        except Exception as exc:
            db.x("UPDATE kb2_knowledges SET status='failed',error=?,"
                 "updated_at=? WHERE id=?",
                 (f"{type(exc).__name__}: {exc}"[:500], db.now(), kid))


# ────────────────────────── retrieval ──────────────────────────

def _spread(items, limit):
    """Keep at most ``limit`` items, sampled evenly across the whole list.

    A long question must not lose its tail just because the head is longer.
    """
    if len(items) <= limit:
        return items
    step = len(items) / float(limit)
    return [items[min(len(items) - 1, int(i * step))] for i in range(limit)]


def query_terms(query, limit=20):
    """Split a natural-language question into FTS-safe terms.

    The trigram tokenizer needs >= 3 characters per token, so Chinese runs are
    expanded into overlapping 3-char windows; ASCII words are kept as-is.
    """
    terms = []
    for run in re.findall(r"[一-鿿]+", query or ""):
        if len(run) <= 4:
            terms.append(run)
        else:
            terms.extend(run[i:i + 3] for i in range(len(run) - 2))
    terms.extend(re.findall(r"[0-9A-Za-z_]{2,}", query or ""))
    out = []
    for term in terms:
        if len(term) >= 3 and term not in out:
            out.append(term)
    return _spread(out, limit)


def _short_terms(query, width=2, limit=24):
    out = []
    for run in re.findall(r"[一-鿿]+", query or ""):
        for i in range(0, max(1, len(run) - width + 1)):
            frag = run[i:i + width]
            if len(frag) == width and frag not in out:
                out.append(frag)
    return _spread(out, limit)


def today():
    return time.strftime("%Y-%m-%d")


# Only enabled documents inside their validity window are searchable.
LIVE_SQL = ("c.enabled=1 AND k.enabled=1 "
            "AND (k.valid_from='' OR k.valid_from<=?) "
            "AND (k.valid_to='' OR k.valid_to>=?)")


CN_DIGITS = "零一二三四五六七八九"
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}


def cn_to_int(text):
    """Parse a simple Chinese numeral (五百, 十五, 一百二十, 两万)."""
    total = section = number = 0
    for ch in text or "":
        if ch in CN_DIGITS:
            number = CN_DIGITS.index(ch)
        elif ch == "两":
            number = 2
        elif ch in CN_UNITS:
            unit = CN_UNITS[ch]
            if unit >= 10000:
                section = (section + number) * unit
                total += section
                section = number = 0
            else:
                section += (number or 1) * unit
                number = 0
        else:
            return None
    return total + section + number


def numbers_in(text):
    """Numbers mentioned in a text, digits and Chinese numerals unified."""
    found = set()
    for token in re.findall(r"[0-9]+(?:\.[0-9]+)?", text or ""):
        try:
            found.add(float(token))
        except ValueError:
            pass
    for token in re.findall(r"[零一二三四五六七八九十百千万亿两]+", text or ""):
        if len(token) < 2:
            continue
        value = cn_to_int(token)
        if value is not None:
            found.add(float(value))
    return found


def int_to_cn(n):
    """Render an integer as Chinese numerals (500 → 五百), up to 亿."""
    n = int(n)
    if n <= 0 or n > 99999999:
        return ""
    digits = "零一二三四五六七八九"
    units = ["", "十", "百", "千"]

    def four(x):
        out, zero = "", False
        for index in (3, 2, 1, 0):
            digit = (x // (10 ** index)) % 10
            if digit:
                if zero and out:
                    out += "零"
                zero = False
                out += digits[digit] + units[index]
            elif out:
                zero = True
        return out

    if n < 10000:
        text = four(n)
        return text[1:] if text.startswith("一十") else text
    high, low = n // 10000, n % 10000
    text = four(high) + "万"
    if low:
        text += ("零" if low < 1000 else "") + four(low)
    return text


def _fts_rows(bid, match, k, day=None):
    day = day or today()
    return db.q(
        "SELECT c.*, k.title AS doc_title, bm25(kb2_chunks_fts) AS rank "
        "FROM kb2_chunks_fts JOIN kb2_chunks c ON c.rid=kb2_chunks_fts.rowid "
        "JOIN kb2_knowledges k ON k.id=c.knowledge_id "
        f"WHERE kb2_chunks_fts MATCH ? AND c.kb_id=? AND {LIVE_SQL} "
        "ORDER BY rank LIMIT ?", (match, bid, day, day, k))


def search(bid, query, top_k=8, day=None):
    query = (query or "").strip()
    if not query:
        return []
    day = day or today()
    k = max(1, min(int(top_k or 8), 30))
    rows = []
    if len(query) >= 3:
        try:
            rows = _fts_rows(bid, '"' + query.replace('"', "") + '"', k, day)
        except Exception:
            rows = []
    if not rows:
        terms = query_terms(query)
        if terms:
            try:
                rows = _fts_rows(bid, " OR ".join(f'"{t}"' for t in terms),
                                 k, day)
            except Exception:
                rows = []
    if not rows:
        like = f"%{query}%"
        rows = db.q(
            "SELECT c.*, k.title AS doc_title, 0 AS rank "
            "FROM kb2_chunks c JOIN kb2_knowledges k ON k.id=c.knowledge_id "
            f"WHERE c.kb_id=? AND {LIVE_SQL} AND "
            "(c.content LIKE ? OR c.heading LIKE ?) LIMIT ?",
            (bid, day, day, like, like, k))
    if not rows:
        # Two-char Chinese fragments never reach the trigram index; score
        # candidates in Python so ranking still follows overlap count.
        terms = _short_terms(query)
        if terms:
            clause = " OR ".join("c.content LIKE ?" for _ in terms)
            rows = db.q(
                "SELECT c.*, k.title AS doc_title, 0 AS rank "
                "FROM kb2_chunks c JOIN kb2_knowledges k ON k.id=c.knowledge_id "
                f"WHERE c.kb_id=? AND {LIVE_SQL} AND ({clause}) LIMIT ?",
                tuple([bid, day, day] + [f"%{t}%" for t in terms] + [k * 4]))
            q_nums = numbers_in(query)
            scored = []
            for row in rows:
                text = (row["content"] or "") + (row["heading"] or "")
                if q_nums:
                    # 五百元 must not answer a question about 八百元.
                    hit_nums = numbers_in(text)
                    if hit_nums and not (hit_nums & q_nums):
                        continue
                scored.append((sum(1 for t in terms if t in text), row))
            scored.sort(key=lambda item: -item[0])
            rows = [row for _, row in scored[:k]]
    if not rows:
        # "500元" should still find "五百元".
        numeric = []
        for value in numbers_in(query):
            if value == int(value):
                numeric.append(str(int(value)))
                chinese = int_to_cn(value)
                if chinese and chinese not in numeric:
                    numeric.append(chinese)
        if numeric:
            clause = " OR ".join("c.content LIKE ?" for _ in numeric)
            rows = db.q(
                "SELECT c.*, k.title AS doc_title, 0 AS rank "
                "FROM kb2_chunks c JOIN kb2_knowledges k ON k.id=c.knowledge_id "
                f"WHERE c.kb_id=? AND {LIVE_SQL} AND ({clause}) LIMIT ?",
                tuple([bid, day, day] + [f"%{t}%" for t in numeric] + [k * 4]))
            wanted = numbers_in(query)
            rows = [r for r in rows
                    if (numbers_in((r["content"] or "") + (r["heading"] or ""))
                        & wanted)]
    return [{"kb_id": r["kb_id"], "knowledge_id": r["knowledge_id"],
             "chunk_id": r["id"], "doc": r["doc_title"],
             "heading": r["heading"], "page": r["page"],
             "content": r["content"], "score": abs(r.get("rank") or 0)}
            for r in rows]


def chunks(kid):
    return db.q("SELECT id,idx,content,heading,page,enabled FROM kb2_chunks "
                "WHERE knowledge_id=? ORDER BY idx", (kid,))


def stats(bid):
    base = db.one("SELECT * FROM kb2_bases WHERE id=?", (bid,))
    if not base:
        return None
    docs = db.one("SELECT COUNT(*) c FROM kb2_knowledges WHERE kb_id=?",
                  (bid,))["c"]
    ready = db.one("SELECT COUNT(*) c FROM kb2_knowledges WHERE kb_id=? AND "
                   "status='ready'", (bid,))["c"]
    chunks_ = db.one("SELECT COUNT(*) c FROM kb2_chunks WHERE kb_id=? AND "
                     "enabled=1", (bid,))["c"]
    disabled = db.one("SELECT COUNT(*) c FROM kb2_chunks WHERE kb_id=? AND "
                      "enabled=0", (bid,))["c"]
    return {"base": base, "documents": docs, "ready": ready,
            "chunks": chunks_, "disabled_chunks": disabled}