# -*- coding: utf-8 -*-
"""PageIndex 集成：每空间一个独立存储目录 + 客户端缓存。

库级隔离靠 doc_id 白名单（local chat 在工具层硬约束），
空间级隔离靠独立 storage 目录 + 独立 client。
非 PDF 文档直写 DocStore（模式来自 kb-platform/ingest/pipelines.py，已验证）。
"""
import os
import threading
import time
import uuid

from . import config, db, llm

_clients = {}
_lock = threading.Lock()

PAGE_LINES = 200          # md 伪页行数（沿用 kb-platform 验证值）


def space_pi_dir(space_id):
    d = os.path.join(config.SPACES, space_id, "pi")
    os.makedirs(d, exist_ok=True)
    return d


def get_client(space_id):
    """空间级 PageIndexClient（storage 隔离）。模型变更时重建。"""
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
    """直写 DocStore（office/md/扫描图等非 PDF 通道）。"""
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
    """原生 PDF 走 flash 索引通道。返回 (doc_id, pages, description)。"""
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
    """读指定页内容，返回 [{page_index, markdown}]。"""
    store = get_store(space_id)
    pages = store.get_pages(doc_id) or []
    want = set(page_indexes)
    return [p for p in pages if p.get("page_index") in want]


def structure_tree(space_id, doc_id):
    store = get_store(space_id)
    return store.get_tree(doc_id)


def md_pages(md_text):
    """md → 伪页：每 PAGE_LINES 行一页，页码必须连续 1..N。

    注意：range 的步进是行偏移（0,200,400…），不能直接拿来当页码；
    之前误用导致 pages.json 页码为 1,201,401…，与树/分页器不一致。
    """
    lines = md_text.split("\n")
    chunks = ["\n".join(lines[i:i + PAGE_LINES])
              for i in range(0, len(lines), PAGE_LINES)]
    if not chunks:
        chunks = [md_text]
    return [{"page_index": n + 1, "markdown": t}
            for n, t in enumerate(chunks)]


def tree_from_md_structure(md_tree_structure, total_pages):
    """md_to_tree 的行号结构 → PI 页号结构（沿用 kb-platform convert 逻辑）。"""
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
