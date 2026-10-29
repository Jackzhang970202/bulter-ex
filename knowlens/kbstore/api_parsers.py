# -*- coding: utf-8 -*-
"""Parser engine registry API."""
from fastapi import APIRouter, HTTPException, Request

from ..platform import auth
from . import parsers

router = APIRouter(prefix="/api/platform/parsers", tags=["parsers"])


@router.get("")
def list_parsers(request: Request):
    auth.current_user(request)
    engines = list(parsers.available_engines().values())
    engines.sort(key=lambda e: (not e.get("available"), e["name"]))
    return {"items": engines, "total": len(engines)}


@router.post("/{engine}/test")
def test_parser(request: Request, engine: str):
    auth.require(request, "editor")
    engines = parsers.available_engines()
    if engine not in engines:
        raise HTTPException(404, "解析引擎不存在")
    info = engines[engine]
    return {"ok": bool(info.get("available")),
            "reason": info.get("reason", ""),
            "file_types": info.get("file_types", [])}
