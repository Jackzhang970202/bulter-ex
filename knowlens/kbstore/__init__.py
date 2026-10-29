# -*- coding: utf-8 -*-
"""Unified knowledge store (P1 foundation).

One storage for every knowledge base: ``libraries`` (knowledge base),
``documents`` (one upload), ``document_versions`` (parsed markdown snapshot)
and ``chunks`` (page / section / text, all in one table).

Migration is additive: existing tables gain columns, new tables are created,
and no current read/write path is changed until the switchover step.
"""

from . import schema  # noqa: F401


def init():
    schema.init()


def migrate_all():
    from . import migrate
    return migrate.run()