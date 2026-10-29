# -*- coding: utf-8 -*-
"""Core types for the adaptive chunker."""
from dataclasses import dataclass, field, replace

DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 80
DEFAULT_SEPARATORS = ("\n\n", "\n", "。")
ABSOLUTE_MAX_SIZE = 7500
MAX_PROTECTED_SIZE = 7500


@dataclass
class Chunk:
    content: str
    start: int
    end: int
    seq: int = 0
    context_header: str = ""

    def embedding_content(self):
        body = self.content.strip()
        if not self.context_header:
            return body
        return self.context_header + "\n\n" + body


@dataclass
class SplitterConfig:
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    separators: list = field(default_factory=list)
    strategy: str = ""
    token_limit: int = 0
    languages: list = field(default_factory=list)

    def clone(self, **kwargs):
        return replace(self, **kwargs)


@dataclass
class ParentChildResult:
    parents: list = field(default_factory=list)
    children: list = field(default_factory=list)   # list[(Chunk, parent_index)]


def normalize_config(cfg):
    """Apply the same defaults as the Go ``NormalizeSplitterConfig``."""
    size = cfg.chunk_size if cfg.chunk_size > 0 else DEFAULT_CHUNK_SIZE
    overlap = cfg.chunk_overlap if cfg.chunk_overlap > 0 else DEFAULT_CHUNK_OVERLAP
    separators = list(cfg.separators) if cfg.separators else list(DEFAULT_SEPARATORS)
    return SplitterConfig(size, overlap, separators, cfg.strategy,
                          cfg.token_limit, list(cfg.languages))