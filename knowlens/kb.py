# -*- coding: utf-8 -*-
"""PageIndex integration: one independent storage directory per space + client cache.

Library-level isolation relies on a doc_id allowlist (hard-enforced at the tool layer for local chat),
space-level isolation relies on a separate storage directory + separate client.
Non-PDF documents are written directly to DocStore (pattern taken from kb-platform/ingest/pipelines.py, already verified).
"""
import os
import threading
import time
import uuid

from . import config, db, llm

_clients = {}
_lock = threading.Lock()

PAGE_LINES = 200          # Lines per md pseudo-page (kb-platform verified value)


def space_pi_dir(space_id):
    d = os.path.join(config.SPACES, space_id, "pi")
    os.makedirs(d, exist_ok=True)
    return d


def get_client(space_id):
    """Space-level PageIndexClient (isolated storage). Rebuilt when the model changes."""
    key = (space_id, llm.model_ref())
    with _lock:
        if _clients.get("key") != key:
            _clients.clear()
            _clients["key"] = key
        c = _clients.get(space_id)
        if c is None:
            from pageindex import PageIndexClient
            model = llm.model_ref()
            c = PageIndexClient(index_model=model, chat_model=model,
                                storage_path=space_pi_dir(space_id))
            _clients[space_id] = c
        return c


def get_store(space_id):
    from pageindex.local_store import DocStore
    return DocStore(space_pi_dir(space_id))


def seed_doc(space_id, name, description, tree, pages, metadata):
    """Write directly to DocStore (non-PDF channels such as office/md/scanned images)."""
    doc_id = "pi-" + uuid.uuid4().hex
    meta = {"id": doc_id, "name": name, "description": description,
            "status": "completed",
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pageNum": len(pages), "folderId": None,
            "metadata": metadata, "mode": "flash"}
    store = get_store(space_id)
    with store.lock():
        store.save_document(doc_id, meta, tree, pages)
    return doc_id


def submit_pdf(space_id, path):
    """Native PDFs go through the flash indexing channel. Returns (doc_id, pages, description)."""
    client = get_client(space_id)
    res = client.submit_document(path)
    m = get_store(space_id).get_meta(res["doc_id"]) or {}
    return res["doc_id"], m.get("pageNum", 0), m.get("description", "")


def delete_doc(space_id, doc_id):
    return get_store(space_id).delete_document(doc_id)


def list_metas(space_id):
    return get_store(space_id).list_metas()


def get_meta(space_id, doc_id):
    return get_store(space_id).get_meta(doc_id)


def get_pages(space_id, doc_id, page_indexes):
    """Read the given pages and return [{page_index, markdown}]."""
    store = get_store(space_id)
    pages = store.get_pages(doc_id) or []
    want = set(page_indexes)
    return [p for p in pages if p.get("page_index") in want]


def structure_tree(space_id, doc_id):
    store = get_store(space_id)
    return store.get_tree(doc_id)


def md_pages(md_text):
    """md -> pseudo pages: one page per PAGE_LINES lines; page numbers must be contiguous 1..N.

    Note: the range step is a line offset (0,200,400...), which cannot be used directly as a page number;
    using it by mistake previously made pages.json page numbers 1,201,401..., inconsistent with the tree/paginator.
    """
    lines = md_text.split("\n")
    chunks = ["\n".join(lines[i:i + PAGE_LINES])
              for i in range(0, len(lines), PAGE_LINES)]
    if not chunks:
        chunks = [md_text]
    return [{"page_index": n + 1, "markdown": t}
            for n, t in enumerate(chunks)]


def tree_from_md_structure(md_tree_structure, total_pages):
    """md_to_tree line-number structure -> PI page-number structure (mirrors kb-platform convert logic)."""
    from pageindex.utils import write_node_id
    flat = []

    def walk(nodes):
        for n in nodes:
            flat.append(n)
            walk(n.get("nodes") or [])
    walk(md_tree_structure)

    def pg(ln):
        return min(total_pages, max(1, ((ln or 1) - 1) // PAGE_LINES + 1))

    bounds = []
    lines_total = None
    for i, n in enumerate(flat):
        end = (flat[i + 1]["line_num"] - 1) if i + 1 < len(flat) else 10 ** 9
        bounds.append((pg(n["line_num"]), pg(end) if end < 10 ** 9
                      else total_pages))
    it = iter(bounds)

    def convert(nodes):
        out = []
        for node in nodes:
            start, end = next(it)
            new = {"title": node.get("title"), "start_index": start,
                   "end_index": end,
                   "summary": (node.get("summary")
                               or node.get("prefix_summary") or "")}
            kids = convert(node.get("nodes") or [])
            if kids:
                new["nodes"] = kids
                new["start_index"] = min([new["start_index"]]
                                         + [k["start_index"] for k in kids])
                new["end_index"] = max([new["end_index"]]
                                        + [k["end_index"] for k in kids])
            out.append(new)
        return out

    structure = convert(md_tree_structure)
    write_node_id(structure)
    return structure


def md_to_tree_async(md_path, model_ref):
    import asyncio
    from pageindex.page_index_md import md_to_tree
    return asyncio.run(md_to_tree(
        md_path=md_path, if_thinning=False, min_token_threshold=5000,
        if_add_node_summary="yes", summary_token_threshold=200,
        model=model_ref, summary_model=model_ref,
        if_add_doc_description="yes", if_add_node_text="yes",
        if_add_node_id="yes"))
