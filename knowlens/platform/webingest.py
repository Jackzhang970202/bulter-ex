# -*- coding: utf-8 -*-
"""Web page ingestion: fetch a URL, extract main content as Markdown, enqueue
the existing ingestion pipeline. No new mandatory dependency: trafilatura is
used when present, otherwise a conservative stdlib extractor runs.
"""
import os
import re
import urllib.parse
from html.parser import HTMLParser

import requests

from .. import config, db, ingest
from . import services, tools

MAX_BYTES = 5 * 1024 * 1024
SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header",
             "aside", "form", "svg", "iframe", "canvas", "button"}
BLOCK_TAGS = {"p", "div", "section", "article", "main", "br", "tr"}
HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.parts = []
        self._skip = 0
        self._in_title = False
        self._buf = []
        self._block = ""
        self._pre = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "pre":
            self._pre += 1
        if tag in HEADINGS:
            self._flush()
            self._block = HEADINGS[tag]
            self._buf = []
        elif tag == "li":
            self._flush()
            self._block = "-"
            self._buf = []
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag == "pre":
            self._pre = max(0, self._pre - 1)
            self._flush()
        elif tag in HEADINGS or tag == "li" or tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()
            return
        if self._skip or not data.strip():
            return
        text = data if self._pre else re.sub(r"\s+", " ", data)
        if self._pre:
            self._buf.append(text)
            return
        if self._block:
            self._buf.append(text.strip())
        else:
            self._buf.append(text.strip())

    def _flush(self):
        text = " ".join(x for x in self._buf if x).strip()
        if text:
            if self._block == "-":
                self.parts.append("- " + text)
            elif self._block:
                self.parts.append(self._block + " " + text)
            else:
                self.parts.append(text)
        self._buf = []
        self._block = ""

    def markdown(self):
        self._flush()
        out, prev = [], ""
        for part in self.parts:
            if part != prev:
                out.append(part)
            prev = part
        return "\n\n".join(out)


def _fallback(html):
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.title, parser.markdown()


def extract(html, url=""):
    """Return (title, markdown) using trafilatura when available."""
    title, markdown = "", ""
    try:
        import trafilatura
        markdown = trafilatura.extract(
            html, output_format="markdown", include_links=True,
            include_tables=True, url=url) or ""
        try:
            meta = trafilatura.extract_metadata(html)
            title = (getattr(meta, "title", "") or "") if meta else ""
        except Exception:
            title = ""
    except Exception:
        markdown = ""
    if len(markdown.strip()) < 80:
        fb_title, fb_md = _fallback(html)
        title = title or fb_title
        if len(fb_md) > len(markdown):
            markdown = fb_md
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html or "",
                      re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
    return (title or url, markdown.strip())


def _request(url):
    """Follow redirects manually so each hop is validated before it is fetched."""
    current = url
    for _ in range(4):
        current = tools.safe_url(current)
        resp = requests.get(current, timeout=(10, 60), allow_redirects=False,
                            headers={"User-Agent":
                                     "Mozilla/5.0 (compatible; KnowLens/0.3)"},
                            stream=True)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location") or ""
            resp.close()
            if not location:
                raise ValueError("重定向缺少 Location")
            current = urllib.parse.urljoin(current, location)
            continue
        return resp
    raise ValueError("重定向次数过多")


def fetch_page(url):
    resp = _request(url)
    if resp.status_code >= 400:
        raise ValueError(f"HTTP {resp.status_code}")
    ctype = (resp.headers.get("content-type") or "").lower()
    if ctype and not any(x in ctype for x in
                         ("text/html", "application/xhtml", "text/plain",
                          "application/xml", "text/xml")):
        raise ValueError(f"不支持的页面类型: {ctype}")
    chunks, size = [], 0
    for chunk in resp.iter_content(65536):
        size += len(chunk)
        if size > MAX_BYTES:
            break
        chunks.append(chunk)
    resp.close()
    raw = b"".join(chunks)
    if "text/plain" in ctype:
        text = raw.decode(resp.encoding or "utf-8", errors="replace")
        first = text.strip().splitlines()[0][:120] if text.strip() else url
        return {"url": resp.url, "title": first, "markdown": text}
    html = raw.decode(resp.encoding or resp.apparent_encoding or "utf-8",
                      errors="replace")
    title, markdown = extract(html, resp.url)
    if len(markdown) < 40:
        raise ValueError("页面正文过短，可能需要登录或由脚本渲染")
    return {"url": resp.url, "title": title[:200], "markdown": markdown}


def _safe_name(title, url):
    base = re.sub(r"[\\/:*?\"<>|\r\n]+", "-", title or "").strip("- ")[:80]
    if not base:
        base = urllib.parse.urlparse(url).netloc[:60] or "webpage"
    return base + ".md"


def ingest_url(space_id, library_id, url, tag_names=None, user_id=""):
    space = db.one("SELECT * FROM spaces WHERE id=?", (space_id,))
    if not space:
        return None, "空间不存在"
    lib = db.one("SELECT * FROM libraries WHERE id=? AND space_id=?",
                 (library_id, space_id))
    if not lib:
        return None, "库不属于该空间"
    page = fetch_page(url)
    did = db.new_id("dc-")
    folder = os.path.join(config.UPLOADS, space_id, did)
    os.makedirs(folder, exist_ok=True)
    name = _safe_name(page["title"], page["url"])
    path = os.path.join(folder, name)
    body = f"# {page['title']}\n\n> 来源：{page['url']}\n\n{page['markdown']}\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    now = db.now()
    db.x("INSERT INTO documents(id,space_id,library_id,name,kind,status,"
         "source_path,vision,created_at,updated_at) "
         "VALUES(?,?,?,?,?,'pending',?,0,?,?)",
         (did, space_id, library_id, name, "md", path, now, now))
    for raw in (tag_names or []):
        tag = str(raw).strip()[:40]
        if not tag:
            continue
        row = db.one("SELECT id FROM tags WHERE space_id=? AND name=?",
                     (space_id, tag))
        if row:
            tid = row["id"]
        else:
            tid = db.new_id("tg-")
            db.x("INSERT INTO tags(id,space_id,name,auto,created_at) "
                 "VALUES(?,?,?,0,?)", (tid, space_id, tag, now))
        db.x("INSERT OR IGNORE INTO doc_tags(document_id,tag_id) VALUES(?,?)",
             (did, tid))
    ingest.start_job(did)
    db.log_event(space_id, did, "ingest_url",
                 {"url": page["url"], "chars": len(page["markdown"])})
    services.audit(user_id, "document.ingest_url", did, page["url"])
    return {"document_id": did, "name": name, "title": page["title"],
            "url": page["url"], "chars": len(page["markdown"])}, ""