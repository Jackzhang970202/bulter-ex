# -*- coding: utf-8 -*-
"""Adaptive chunker ported from Knowledge Hub (Go ``internal/infrastructure/chunker``).

Public entry points mirror the source package:

* ``split(text, cfg)`` — single-pass adaptive split
* ``split_parent_child(text, cfg, parent_size, child_size)``
* ``preview(text, cfg)`` — diagnostics for the preview endpoint

Chunk offsets are rune offsets into the normalized text and satisfy
``end - start == len(content)``.
"""

from .model import Chunk, ParentChildResult, SplitterConfig, normalize_config
from .strategy import (TierHeading, TierHeuristic, TierLegacy, diagnostics,
                       split, split_parent_child)

__all__ = ["Chunk", "ParentChildResult", "SplitterConfig", "normalize_config",
           "split", "split_parent_child", "diagnostics",
           "TierHeading", "TierHeuristic", "TierLegacy"]


def preview(text, config=None):
    from .strategy import preview as _preview
    return _preview(text, config)


def chunk_markdown(text, chunk_size=512, chunk_overlap=80, strategy="auto",
                   separators=None, token_limit=0, languages=None):
    """Convenience wrapper used by the kbstore ingestion path."""
    cfg = SplitterConfig(chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                         strategy=strategy, separators=separators or [],
                         token_limit=token_limit, languages=languages or [])
    return split(text, cfg)