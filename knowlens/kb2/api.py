# -*- coding: utf-8 -*-
"""HTTP API for the chunk-level knowledge base."""
import os

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile

from .. import db
from ..platform import auth, services
from . import engine

router = APIRouter(prefix="/api/kb2", tags=["kb2"])


@router.get("/bases")
def list_bases(request: Request):
    auth.current_user(request)
    return {"items": engine.list_bases()}


@router.post("/bases")
def create_base(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "知识库名称不能为空")
    row = engine.create_base(
        name, payload.get("description") or "",
        int(payload.get("chunk_size") or 512),
        int(payload.get("chunk_overlap") or 80))
    services.audit(user["id"], "kb2.create", row["id"], name)
    return row


@router.delete("/bases/{bid}")
def delete_base(request: Request, bid: str):
    user = auth.require(request, "editor")
    if not db.one("SELECT id FROM kb2_bases WHERE id=?", (bid,)):
        raise HTTPException(404, "知识库不存在")
    engine.delete_base(bid)
    services.audit(user["id"], "kb2.delete", bid, "")
    return {"ok": True}


@router.patch("/bases/{bid}")
def update_base(request: Request, bid: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = engine.update_base(
        bid, name=payload.get("name"),
        description=payload.get("description"),
        chunk_size=payload.get("chunk_size"),
        chunk_overlap=payload.get("chunk_overlap"))
    if not row:
        raise HTTPException(404, "知识库不存在")
    services.audit(user["id"], "kb2.update", bid, ",".join(payload))
    return row


@router.get("/bases/{bid}")
def get_base(request: Request, bid: str):
    auth.current_user(request)
    stat = engine.stats(bid)
    if not stat:
        raise HTTPException(404, "知识库不存在")
    return stat


@router.get("/bases/{bid}/documents")
def documents(request: Request, bid: str):
    auth.current_user(request)
    return {"items": engine.list_docs(bid)}


@router.post("/bases/{bid}/upload")
async def upload(request: Request, bid: str, files: list[UploadFile] = File(...)):
    user = auth.require(request, "editor")
    if not db.one("SELECT id FROM kb2_bases WHERE id=?", (bid,)):
        raise HTTPException(404, "知识库不存在")
    out = []
    for f in files[:10]:
        data = await f.read()
        if len(data) > 50 * 1024 * 1024:
            out.append({"name": f.filename, "error": "超过 50MB"})
            continue
        row, error = engine.add_file(bid, f.filename, data, user["id"])
        if error:
            out.append({"name": f.filename, "error": error})
        else:
            out.append(row)
    services.audit(user["id"], "kb2.upload", bid, f"{len(out)} files")
    return {"items": out}


@router.delete("/documents/{kid}")
def delete_document(request: Request, kid: str):
    user = auth.require(request, "editor")
    if not engine.delete_doc(kid):
        raise HTTPException(404, "文档不存在")
    services.audit(user["id"], "kb2.doc.delete", kid, "")
    return {"ok": True}


@router.patch("/documents/{kid}")
def update_document(request: Request, kid: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = engine.update_doc(kid, tags=payload.get("tags"),
                            enabled=payload.get("enabled"),
                            valid_from=payload.get("valid_from"),
                            valid_to=payload.get("valid_to"))
    if not row:
        raise HTTPException(404, "文档不存在")
    services.audit(user["id"], "kb2.doc.update", kid, ",".join(payload))
    return row


@router.post("/documents/{kid}/reindex")
def reindex_document(request: Request, kid: str):
    user = auth.require(request, "editor")
    row = engine.reindex_doc(kid)
    if not row:
        raise HTTPException(404, "文档不存在")
    services.audit(user["id"], "kb2.doc.reindex", kid, "")
    return row


@router.get("/documents/{kid}/versions")
def document_versions(request: Request, kid: str):
    auth.current_user(request)
    return {"items": engine.list_versions(kid)}


@router.post("/documents/{kid}/version")
async def upload_version(request: Request, kid: str,
                         file: UploadFile = File(...)):
    user = auth.require(request, "editor")
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "超过 50MB")
    row, error = engine.add_version(kid, file.filename, data, user["id"])
    if error:
        raise HTTPException(400, error)
    services.audit(user["id"], "kb2.doc.version", kid,
                   f"v{row.get('version')}")
    return row


@router.post("/documents/{kid}/rollback/{version}")
def rollback_document(request: Request, kid: str, version: int):
    user = auth.require(request, "editor")
    row, error = engine.rollback(kid, version)
    if error:
        raise HTTPException(400, error)
    services.audit(user["id"], "kb2.doc.rollback", kid, f"v{version}")
    return row


@router.post("/chunks/{cid}/toggle")
def toggle_chunk(request: Request, cid: str, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    enabled = payload.get("enabled")
    row = engine.set_chunk_enabled(cid, True if enabled is None else enabled)
    if not row:
        raise HTTPException(404, "分块不存在")
    services.audit(user["id"], "kb2.chunk.toggle", cid, str(row["enabled"]))
    return row


@router.get("/documents/{kid}/chunks")
def document_chunks(request: Request, kid: str):
    auth.current_user(request)
    return {"items": engine.chunks(kid)}


@router.post("/bases/{bid}/search")
def search(request: Request, bid: str, payload: dict = Body(default={})):
    auth.current_user(request)
    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "检索内容不能为空")
    return {"items": engine.search(bid, query,
                                   int(payload.get("top_k") or 8))}