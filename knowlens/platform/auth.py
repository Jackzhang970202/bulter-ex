# -*- coding: utf-8 -*-
"""Lightweight identity: X-User-Id header, three roles, audit helper."""
from fastapi import HTTPException, Request

from .. import db

ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}


def current_user(request: Request) -> dict:
    uid = (request.headers.get("X-User-Id")
           or request.query_params.get("user_id") or "u_admin").strip()
    row = db.one("SELECT * FROM pf_users WHERE id=?", (uid,))
    if not row or not row.get("active"):
        raise HTTPException(401, f"未知用户: {uid}")
    return row


def require(request: Request, role: str = "editor") -> dict:
    user = current_user(request)
    if ROLE_RANK.get(user["role"], 0) < ROLE_RANK.get(role, 2):
        raise HTTPException(403, f"需要 {role} 及以上权限")
    return user


def can(user: dict, role: str) -> bool:
    return ROLE_RANK.get(user.get("role"), 0) >= ROLE_RANK.get(role, 2)