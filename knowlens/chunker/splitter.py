# -*- coding: utf-8 -*-
"""Recursive splitter with protected units, table headers and semantic overlap.

Ported from ``internal/infrastructure/chunker/splitter.go`` and
``header_tracker.go``. Offsets are character (rune) offsets.
"""
from dataclasses import dataclass

from .model import ABSOLUTE_MAX_SIZE, MAX_PROTECTED_SIZE, Chunk
from .patterns import PROTECTED, TABLE_HEADER


@dataclass
class Unit:
    text: str
    start: int
    end: int


def protected_spans(text):
    spans = []
    for pattern in PROTECTED:
        for match in pattern.finditer(text):
            if match.end() > match.start():
                spans.append((match.start(), match.end()))
    if not spans:
        return []
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    merged, last_end = [], -1
    for start, end in spans:
        if start >= last_end:
            merged.append((start, end))
            last_end = end
    return merged


def split_by_separators(text, separators, chunk_size):
    if not text or not separators:
        return [text]
    if chunk_size > 0 and len(text) <= chunk_size:
        return [text]
    for index, sep in enumerate(separators):
        if not sep:
            continue
        pattern = _sep_pattern(sep)
        if not pattern.search(text):
            continue
        pieces, pos = [], 0
        for match in pattern.finditer(text):
            pieces.append(text[pos:match.start()])
            pieces.append(match.group(0))
            pos = match.end()
        pieces.append(text[pos:])
        pieces = [p for p in pieces if p]
        if len(pieces) <= 1:
            continue
        remaining = separators[index + 1:]
        out = []
        for piece in pieces:
            if chunk_size > 0 and len(piece) > chunk_size and remaining:
                out.extend(split_by_separators(piece, remaining, chunk_size))
            else:
                out.append(piece)
        return out
    return [text]


def _sep_pattern(sep):
    import re
    return re.compile(re.escape(sep))


def _force_split(text, start, size):
    """Split an oversized atomic piece at a newline/space when possible."""
    out, offset, total = [], 0, len(text)
    while offset < total:
        end = min(offset + size, total)
        if end < total:
            floor = max(offset, end - 200)
            for pos in range(end - 1, floor - 1, -1):
                if text[pos] in ("\n", " "):
                    end = pos + 1
                    break
        out.append(Unit(text[offset:end], start + offset, start + end))
        offset = end
    return out


def build_units(text, spans, separators, chunk_size):
    units, pos = [], 0
    for start, end in spans:
        if start > pos:
            units.extend(_split_plain(text[pos:start], pos, separators,
                                      chunk_size))
        piece = text[start:end]
        if len(piece) > MAX_PROTECTED_SIZE:
            units.extend(_force_split(piece, start, MAX_PROTECTED_SIZE))
        else:
            units.append(Unit(piece, start, end))
        pos = end
    if pos < len(text):
        units.extend(_split_plain(text[pos:], pos, separators, chunk_size))
    return units


def _split_plain(text, base, separators, chunk_size):
    out, offset = [], 0
    for part in split_by_separators(text, separators, chunk_size):
        out.append(Unit(part, base + offset, base + offset + len(part)))
        offset += len(part)
    return out


class HeaderTracker:
    """Keeps a Markdown table header active across split units."""

    def __init__(self):
        self.header = ""
        self.ended_this_unit = False

    def update(self, text):
        self.ended_this_unit = False
        stripped = text.strip()
        if self.header and stripped and not stripped.startswith("|"):
            self.header = ""
            self.ended_this_unit = True
        match = TABLE_HEADER.search(text)
        if match:
            self.header = match.group(0)


def merge_units(units, chunk_size, overlap):
    if not units:
        return []
    tracker = HeaderTracker()
    chunks, current, cur_len = [], [], 0

    def flush():
        nonlocal current, cur_len
        if current:
            chunks.append(_build(current, len(chunks)))
            current, cur_len = [], 0

    for unit in units:
        length = len(unit.text)
        if length > ABSOLUTE_MAX_SIZE:
            flush()
            tracker.update(unit.text)
            for part in _force_split(unit.text, unit.start, ABSOLUTE_MAX_SIZE):
                chunks.append(Chunk(part.text, part.start, part.end,
                                    len(chunks)))
            continue

        tracker.update(unit.text)
        if tracker.ended_this_unit:
            flush()
        header = tracker.header
        if len(header) > chunk_size:
            header = ""

        if cur_len + length + len(header) > chunk_size and current:
            flush()
            current, cur_len = _compute_overlap(current or [unit], overlap,
                                                chunk_size, length)
            if header and header not in _units_text(current) \
                    and header not in unit.text:
                start = current[0].start if current else unit.start
                current.insert(0, Unit(header, start, start))
                cur_len += len(header)

        if cur_len + length > ABSOLUTE_MAX_SIZE:
            flush()

        current.append(unit)
        cur_len += length

    flush()
    return chunks


def _units_text(units):
    return "".join(u.text for u in units)


def _build(units, seq):
    return Chunk(_units_text(units), units[0].start, units[-1].end, seq)


def _compute_overlap(current, overlap, chunk_size, next_len):
    if overlap <= 0:
        return [], 0
    limit = min(overlap, max(0, chunk_size - next_len))
    if limit <= 0:
        return [], 0

    # Collect a tail window of source-backed text.
    tail, remaining = [], limit + 4
    for unit in reversed(current):
        if remaining <= 0:
            break
        text = unit.text
        size = len(text)
        if size == 0:
            continue
        if size <= remaining:
            tail.append(unit)
            remaining -= size
        else:
            cut = size - remaining
            tail.append(Unit(text[cut:], unit.start + cut, unit.end))
            remaining = 0
    if not tail:
        return [], 0
    tail.reverse()
    window = _units_text(tail)
    start_at = max(0, len(window) - limit)
    boundary = _semantic_boundary(window, start_at)
    if boundary is None:
        return [], 0
    kept_text = window[boundary:]
    if not kept_text.strip() or len(kept_text) > limit:
        return [], 0
    # Map back to a single synthetic unit (source-backed suffix).
    total_start = tail[0].start
    return [Unit(kept_text, total_start + boundary,
                 total_start + boundary + len(kept_text))], len(kept_text)


def _semantic_boundary(text, search_from):
    """Earliest paragraph/line/sentence boundary ending at or after search_from."""
    best = None
    candidates = []
    idx = text.find("\n\n", search_from)
    if idx >= 0:
        candidates.append((0, idx + 2))
    for token in ("\n",):
        idx = text.find(token, search_from)
        if idx >= 0:
            candidates.append((1, idx + 1))
    for token in ("。", "！", "？", ". ", "? ", "! "):
        idx = text.find(token, search_from)
        if idx >= 0:
            candidates.append((2, idx + len(token)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    best = candidates[0][1]
    return best

def split_text(text, cfg):
    """Recursive splitter entry point (mirrors Go ``SplitText``)."""
    if not text:
        return []
    size = cfg.chunk_size if cfg.chunk_size > 0 else 512
    separators = cfg.separators or ["\n\n", "\n", "。"]
    spans = protected_spans(text)
    units = build_units(text, spans, separators, size)
    return merge_units(units, size, max(0, cfg.chunk_overlap))
