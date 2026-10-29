# -*- coding: utf-8 -*-
"""Boundary-driven chunking (Tier 2), ported from ``heuristic_splitter.go``."""
from . import patterns
from .model import Chunk
from .patterns import chapter_patterns
from .splitter import protected_spans, split_text


def find_boundaries(text, languages):
    bounds = []
    for index, ch in enumerate(text):
        if ch == "\f":
            bounds.append((index, patterns.PRIO_FORM_FEED))

    chapter_res = chapter_patterns(languages)
    pos, in_fence = 0, False
    lines = text.split("\n")
    for index, line in enumerate(lines):
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            added = False
            for pattern in chapter_res:
                if pattern.match(line):
                    bounds.append((pos, patterns.PRIO_CHAPTER_MARKER))
                    added = True
                    break
            if not added and patterns.NUMBERED_SECTION.match(line):
                bounds.append((pos, patterns.PRIO_NUMBERED_HEAD))
                added = True
            if not added and patterns.ALL_CAPS_HEADING.match(line):
                bounds.append((pos, patterns.PRIO_ALL_CAPS_HEADING))
                added = True
            if not added and patterns.VISUAL_SEPARATOR.match(line):
                bounds.append((pos, patterns.PRIO_VISUAL_SEP))
                added = True
            if not added and patterns.PAGE_FOOTER.match(line):
                bounds.append((pos, patterns.PRIO_PAGE_FOOTER))
        pos += len(line)
        if index < len(lines) - 1:
            pos += 1

    for match in patterns.EXCESSIVE_BLANKS.finditer(text):
        bounds.append((match.end(), patterns.PRIO_BLANK_BLOCK))
    if not bounds:
        return []
    bounds.sort(key=lambda item: (item[0], -item[1]))
    deduped, prev = [], -1
    for start, priority in bounds:
        if start != prev:
            deduped.append((start, priority))
            prev = start
    return deduped


def drop_inside_spans(bounds, spans):
    if not spans:
        return bounds
    out = []
    for start, priority in bounds:
        inside = False
        for span_start, span_end in spans:
            if span_start >= start:
                break
            if start < span_end:
                inside = True
                break
        if not inside:
            out.append((start, priority))
    return out


def _append(out, text, start, end, seq):
    if end <= start:
        return seq
    raw = text[start:end]
    if not raw.strip():
        return seq
    out.append(Chunk(raw, start, end, seq))
    return seq + 1


def _apply_overlap(cur_end, overlap, bounds):
    if overlap <= 0:
        return cur_end
    target = max(0, cur_end - overlap)
    window_start = max(0, cur_end - 2 * overlap)
    best = -1
    for start, _ in bounds:
        if window_start <= start < cur_end and start > best:
            best = start
    if best >= 0:
        return best
    return target


def split_by_heuristics(text, cfg, profile=None):
    if not text:
        return []
    total = len(text)
    if total <= cfg.chunk_size:
        return split_text(text, cfg)

    bounds = find_boundaries(text, cfg.languages)
    spans = protected_spans(text)
    if spans:
        bounds = drop_inside_spans(bounds, spans)
    if not bounds:
        return split_text(text, cfg)

    bounds = bounds + [(total, 0)]
    if bounds[0][0] != 0:
        bounds = [(0, 0)] + bounds

    out, seq = [], 0
    chunk_start = bounds[0][0]
    cur_end = chunk_start
    min_chunk = max(cfg.chunk_size // 4, 50)

    for index in range(1, len(bounds)):
        next_end = bounds[index][0]
        block_len = next_end - cur_end
        if block_len > cfg.chunk_size:
            if cur_end - chunk_start > 0:
                seq = _append(out, text, chunk_start, cur_end, seq)
                chunk_start = cur_end
            sub_start = cur_end
            for sub in split_text(text[sub_start:next_end], cfg):
                if sub.content.strip():
                    out.append(Chunk(sub.content, sub_start + sub.start,
                                     sub_start + sub.end, seq))
                    seq += 1
            cur_end = next_end
            chunk_start = next_end
            continue

        accumulated = next_end - chunk_start
        if accumulated > cfg.chunk_size and cur_end - chunk_start >= min_chunk:
            seq = _append(out, text, chunk_start, cur_end, seq)
            chunk_start = _apply_overlap(cur_end, cfg.chunk_overlap, bounds)
        cur_end = next_end

    if cur_end > chunk_start:
        seq = _append(out, text, chunk_start, cur_end, seq)
    return out