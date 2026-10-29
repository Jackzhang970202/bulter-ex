# -*- coding: utf-8 -*-
"""Model source configuration API."""
from fastapi import APIRouter, Body, HTTPException, Request

from ..platform import auth, services
from . import models

router = APIRouter(prefix="/api/platform/models", tags=["models"])


@router.get("")
def list_models(request: Request, kind: str = ""):
    auth.current_user(request)
    return {"items": models.list_models(kind), "kinds": list(models.KINDS),
            "kind_labels": models.KIND_LABELS}


@router.post("")
def create_model(request: Request, payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    try:
        row = models.create_model(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    services.audit(user["id"], "model.create", row["id"], row["name"])
    return row


@router.patch("/{model_id}")
def update_model(request: Request, model_id: str,
                 payload: dict = Body(default={})):
    user = auth.require(request, "editor")
    row = models.update_model(model_id, payload)
    if not row:
        raise HTTPException(404, "模型不存在")
    services.audit(user["id"], "model.update", model_id,
                   ",".join(payload.keys()))
    return row


@router.delete("/{model_id}")
def delete_model(request: Request, model_id: str):
    user = auth.require(request, "editor")
    if not models.delete_model(model_id):
        raise HTTPException(404, "模型不存在")
    services.audit(user["id"], "model.delete", model_id, "")
    return {"ok": True}


@router.post("/{model_id}/test")
def test_model(request: Request, model_id: str,
               payload: dict = Body(default={})):
    auth.require(request, "editor")
    return models.test_model(model_id, payload)


@router.get("/effective")
def effective(request: Request, kind: str = "", model_id: str = ""):
    """Which model would be used for a capability (never returns the key)."""
    auth.current_user(request)
    if kind not in models.KINDS:
        raise HTTPException(400, "kind 不合法")
    row = models.resolve(kind, model_id)
    return {"id": row.get("id", ""), "kind": kind,
            "name": row.get("name", ""), "provider": row.get("provider", ""),
            "model_name": row.get("model_name", ""),
            "base_url": row.get("base_url", ""),
            "configured": models.configured(kind)}