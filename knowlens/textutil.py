# -*- coding: utf-8 -*-
"""Text normalization and candidate dedup keys."""
import hashlib
import re


def norm_text(s: str) -> str:
    """Strip whitespace/punctuation and lowercase; used for synonym comparison."""
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def candidate_key(kind: str, statement: str) -> str:
    return hashlib.sha1(
        (kind + "|" + norm_text(statement)).encode("utf-8")).hexdigest()[:16]