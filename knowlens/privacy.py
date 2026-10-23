# -*- coding: utf-8 -*-
"""Sensitive information masking (keys/tokens/phone numbers/ID numbers/emails).

Rules can be extended in the filter_rules table; the default rules are written on first startup.
Applied to externally displayed/distilled material such as imported dev sessions and candidate evidence.
"""
import re

from . import db

DEFAULT_RULES = [
    (r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"']?[A-Za-z0-9\-_.]{12,}",
     "***"),
    (r"sk-[A-Za-z0-9\-_.]{16,}", "sk-***"),
    (r"(?i)(bearer\s+)[A-Za-z0-9\-_.]{20,}", "Bearer ***"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "***手机号***"),
    (r"(?<!\d)\d{17}[\dXx](?!\d)", "***身份证号***"),
    (r"[\w.\-]+@[\w\-]+\.[\w.\-]+", "***邮箱***"),
]


def seed():
    try:
        if db.one("SELECT COUNT(*) c FROM filter_rules")["c"]:
            return
        for pattern, _ in DEFAULT_RULES:
            db.x("INSERT INTO filter_rules(id,space_id,pattern,category,note,"
                 "active,created_at) VALUES(?,?,?,?,?,1,?)",
                 (db.new_id("fr-"), "", pattern, "默认规则",
                  "内置脱敏规则", db.now()))
    except Exception:
        pass


def mask(text, space_id=""):
    if not text:
        return text
    out = str(text)
    for pattern, repl in DEFAULT_RULES:
        try:
            out = re.sub(pattern, repl, out)
        except re.error:
            pass
    try:
        rows = db.q("SELECT pattern FROM filter_rules WHERE active=1 "
                    "AND (space_id=? OR space_id='')", (space_id,))
        for r in rows:
            try:
                out = re.sub(r["pattern"], "***", out)
            except re.error:
                pass
    except Exception:
        pass
    return out