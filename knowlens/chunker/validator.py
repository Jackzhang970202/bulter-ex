# -*- coding: utf-8 -*-
"""Tier output validator (ported from ``validator.go``)."""
from dataclasses import dataclass


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""


def validate_chunks(chunks, total_chars, chunk_size):
    if not chunks:
        return ValidationResult(False, "no chunks produced")
    if len(chunks) == 1 and total_chars > 2 * chunk_size:
        return ValidationResult(False, "single chunk for large document")

    lengths = [len(c.content) for c in chunks]
    max_len, min_len = max(lengths), min(lengths)

    tiny = sum(1 for i, value in enumerate(lengths)
               if i != len(lengths) - 1 and value < 50)
    if tiny > len(chunks) // 4 and tiny > 2:
        return ValidationResult(False, "too many tiny chunks")
    if max_len < chunk_size // 4 and total_chars > chunk_size:
        return ValidationResult(False, "all chunks far below target size")
    if chunk_size > 0 and max_len > 2 * chunk_size:
        return ValidationResult(False, "chunk exceeds 2x target size")
    return ValidationResult(True)