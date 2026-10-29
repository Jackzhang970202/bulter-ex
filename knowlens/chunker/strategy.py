# -*- coding: utf-8 -*-
"""Adaptive strategy chain and parent-child splitting.

Ported from ``strategy.go``: normalize config, pick a tier chain, run each
tier until the output passes validation, and fall back to the recursive
splitter as the ultimate safety net.
"""
from .heading import split_by_headings
from .heuristic import split_by_heuristics
from .model import (DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE,
                    DEFAULT_SEPARATORS, Chunk, ParentChildResult,
                    SplitterConfig, normalize_config)
from .profiler import (TIER_HEADING, TIER_HEURISTIC, TIER_LEGACY,
                       profile_document, select_strategy)
from .splitter import split_text
from .tokens import LANG_MIXED, approx_tokens, chars_for_token_limit
from .validator import validate_chunks

TierHeading = TIER_HEADING
TierHeuristic = TIER_HEURISTIC
TierLegacy = TIER_LEGACY

STRATEGY_AUTO = "auto"
STRATEGY_HEADING = "heading"
STRATEGY_HEURISTIC = "heuristic"
STRATEGY_RECURSIVE = "recursive"
STRATEGY_LEGACY = "legacy"


def normalize_line_endings(text):
    if "\r" not in text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def ensure_defaults(cfg):
    cfg = normalize_config(cfg)
    if cfg.token_limit > 0:
        lang = cfg.languages[0] if cfg.languages else LANG_MIXED
        budget = chars_for_token_limit(cfg.token_limit, lang)
        if budget > 0 and budget < cfg.chunk_size:
            cfg = cfg.clone(chunk_size=budget)
    if cfg.chunk_size > 0 and cfg.chunk_overlap > cfg.chunk_size // 2:
        cfg = cfg.clone(chunk_overlap=cfg.chunk_size // 2)
    return cfg


def derive_parent_child_configs(base, parent_size=0, child_size=0):
    parent_size = parent_size or 4096
    child_size = child_size or 384
    parent = SplitterConfig(parent_size, base.chunk_overlap,
                            list(base.separators), base.strategy, 0,
                            list(base.languages))
    child = SplitterConfig(child_size, child_size // 5,
                           list(base.separators), base.strategy,
                           base.token_limit, list(base.languages))
    return parent, child


def _resolve_chain(text, cfg):
    strategy = (cfg.strategy or "").lower()
    if strategy == STRATEGY_HEADING:
        return [TIER_HEADING, TIER_LEGACY], None
    if strategy == STRATEGY_HEURISTIC:
        return [TIER_HEURISTIC, TIER_LEGACY], None
    if strategy in (STRATEGY_RECURSIVE, STRATEGY_LEGACY):
        return [TIER_LEGACY], None
    profile = profile_document(text)
    return select_strategy(profile), profile


def _run_tier(tier, text, cfg, profile):
    if tier == TIER_HEADING:
        return split_by_headings(text, cfg, profile)
    if tier == TIER_HEURISTIC:
        return split_by_heuristics(text, cfg, profile)
    return split_text(text, cfg)


def split(text, cfg=None):
    cfg = ensure_defaults(cfg or SplitterConfig())
    text = normalize_line_endings(text or "")
    if not text:
        return []
    chain, profile = _resolve_chain(text, cfg)
    last = []
    for tier in chain:
        out = _run_tier(tier, text, cfg, profile)
        result = validate_chunks(out, len(text), cfg.chunk_size)
        if result.ok:
            return out
        last = out
    return last or split_text(text, cfg)


def merge_breadcrumbs(parent, child):
    if not parent:
        return child
    if not child:
        return parent
    parent_lines = parent.split("\n")
    child_lines = child.split("\n")
    if parent_lines and child_lines and \
            parent_lines[-1].strip() == child_lines[0].strip():
        child_lines = child_lines[1:]
    if not child_lines:
        return parent
    return parent + "\n" + "\n".join(child_lines)


def split_parent_child(text, cfg=None, parent_size=4096, child_size=384):
    base = ensure_defaults(cfg or SplitterConfig())
    parent_cfg, child_cfg = derive_parent_child_configs(base, parent_size,
                                                        child_size)
    parents = split(text, parent_cfg)
    out = ParentChildResult(parents=parents, children=[])
    for parent_index, parent in enumerate(parents):
        for sub in split(parent.content, child_cfg):
            sub.start += parent.start
            sub.end += parent.start
            sub.context_header = merge_breadcrumbs(parent.context_header,
                                                   sub.context_header)
            out.children.append((sub, parent_index))
    return out


def diagnostics(text, cfg=None):
    """Preview payload: chain, selected tier, rejections, profile, chunks."""
    cfg = ensure_defaults(cfg or SplitterConfig())
    text = normalize_line_endings(text or "")
    profile = profile_document(text)
    chain = _resolve_chain(text, cfg)[0] if text else [TIER_LEGACY]
    rejected, selected, chunks = [], None, []
    if text:
        for tier in chain:
            candidate = _run_tier(tier, text, cfg,
                                  profile if cfg.strategy in ("", STRATEGY_AUTO)
                                  else None)
            result = validate_chunks(candidate, len(text), cfg.chunk_size)
            if result.ok:
                selected, chunks = tier, candidate
                break
            rejected.append({"tier": tier, "reason": result.reason})
            chunks = candidate
    lang = cfg.languages[0] if cfg.languages else LANG_MIXED
    return {
        "selected_tier": selected,
        "tier_chain": chain,
        "rejected": rejected,
        "profile": {
            "total_chars": profile.total_chars,
            "total_lines": profile.total_lines,
            "md_heading_total": profile.md_heading_total,
            "form_feed_count": profile.form_feed_count,
            "numbered_section_count": profile.numbered_section_count,
            "chapter_markers": (profile.german_chapter_count
                                + profile.english_chapter_count
                                + profile.chinese_chapter_count),
            "languages": profile.detected_langs,
        },
        "stats": {
            "chunks": len(chunks),
            "avg_chars": round(sum(len(c.content) for c in chunks)
                               / len(chunks), 1) if chunks else 0,
            "max_chars": max((len(c.content) for c in chunks), default=0),
            "min_chars": min((len(c.content) for c in chunks), default=0),
        },
        "chunks": [{"seq": c.seq, "start": c.start, "end": c.end,
                    "chars": len(c.content),
                    "tokens": approx_tokens(c.content, lang),
                    "context_header": c.context_header,
                    "preview": c.content[:200]} for c in chunks[:50]],
    }


def preview(text, cfg=None):
    return diagnostics(text, cfg)