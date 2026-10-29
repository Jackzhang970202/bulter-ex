# -*- coding: utf-8 -*-
"""Chunk-level knowledge base (SQLite + FTS5), coexisting with PageIndex."""

from . import engine, schema  # noqa: F401


def init():
    engine.init()