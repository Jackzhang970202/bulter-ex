# -*- coding: utf-8 -*-
"""Heading-aware chunking (Tier 1), ported from ``heading_splitter.go``."""
import copy

from .model import Chunk
from .patterns import MARKDOWN_HEADING
from .profiler import profile_document
from .splitter import split_text


class HeadingHierarchy:
    def __init__(self):
        self.stack = [""] * 6
        self.depth = 0

    def observe(self, line):
        match = MARKDOWN_HEADING.match(line)
        if not match:
            return 0, ""
        level = len(match.group(1))
        if level < 1 or level > 6:
            return 0, ""
        heading = match.group(2).strip()
        self.stack[level - 1] = heading
        for index in range(level, 6):
            self.stack[index] = ""
        self.depth = max((i + 1 for i, text in enumerate(self.stack) if text),
                         default=0)
        return level, heading

    def breadcrumb(self):
        return " > ".join(t for t in self.stack[:self.depth] if t)

    def breadcrumb_with_hashes(self):
        lines = []
        for index, text in enumerate(self.stack[:self.depth]):
            if text:
                lines.append("#" * (index + 1) + " " + text)
        return "\n".join(lines)


def _heading_line(line):
    match = MARKDOWN_HEADING.match(line)
    if not match:
        return 0
    return len(match.group(1))


def find_heading_boundaries(text, primary_level):
    bounds = [(0, "")]
    pos, in_fence = 0, False
    lines = text.split("\n")
    for index, line in enumerate(lines):
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            level = _heading_line(line)
            if 1 <= level <= primary_level:
                if pos > 0:
                    bounds.append((pos, line))
                else:
                    bounds[0] = (0, line)
        pos += len(line)
        if index < len(lines) - 1:
            pos += 1
    return bounds


def observe_sub_headings(section, primary_level, hierarchy):
    in_fence = False
    for line in section.split("\n"):
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        level = _heading_line(line)
        if level > primary_level:
            hierarchy.observe(line)


def _sub_breadcrumbs(section, base, primary, start_hierarchy):
    """Breadcrumb snapshots at deep-heading offsets inside a section."""
    hierarchy = copy.deepcopy(start_hierarchy)
    out = [(0, start_hierarchy.breadcrumb_with_hashes())]
    pos, in_fence = 0, False
    for line in section.split("\n"):
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            level = _heading_line(line)
            if level >= 1:
                hierarchy.observe(line)
                if pos > 0:
                    out.append((pos, hierarchy.breadcrumb_with_hashes()))
        pos += len(line) + 1
    return out


def _breadcrumb_at(snapshots, offset, fallback):
    current = fallback
    for start, breadcrumb in snapshots:
        if start <= offset:
            current = breadcrumb or fallback
        else:
            break
    return current


def coalesce_tiny(chunks, chunk_size):
    if len(chunks) <= 1 or chunk_size <= 0:
        return chunks
    target = max(chunk_size // 2, 200)
    out = [chunks[0]]
    cur = out[0]
    cur_len = len(cur.content)
    for nxt in chunks[1:]:
        shared = _common_prefix(cur.context_header, nxt.context_header)
        if (shared and cur.end == nxt.start and cur_len < target
                and cur_len + len(nxt.content) <= chunk_size):
            cur.content += nxt.content
            cur.context_header = shared
            cur.end = nxt.end
            cur_len += len(nxt.content)
            continue
        cur = nxt
        out.append(cur)
        cur_len = len(nxt.content)
    for index, chunk in enumerate(out):
        chunk.seq = index
    return out


def _common_prefix(a, b):
    if a == b:
        return a
    left, right = a.split("\n"), b.split("\n")
    common = 0
    for i in range(min(len(left), len(right))):
        if left[i] != right[i]:
            break
        common = i + 1
    return "\n".join(left[:common]) if common else ""


def split_by_headings(text, cfg, profile=None):
    if not text:
        return []
    profile = profile or profile_document(text)
    primary = profile.dominant_heading_level()
    if primary == 0:
        return split_text(text, cfg)
    bounds = find_heading_boundaries(text, primary)
    if len(bounds) <= 1:
        return split_text(text, cfg)

    hierarchy = HeadingHierarchy()
    out, seq = [], 0
    for index, (start, line) in enumerate(bounds):
        end = bounds[index + 1][0] if index + 1 < len(bounds) else len(text)
        if line:
            hierarchy.observe(line)
        breadcrumb = hierarchy.breadcrumb_with_hashes()
        section_start = copy.deepcopy(hierarchy)
        section = text[start:end]
        observe_sub_headings(section, primary, hierarchy)
        if not section:
            continue
        if len(breadcrumb) + 2 + len(section) <= cfg.chunk_size:
            out.append(Chunk(section, start, end, seq, breadcrumb))
            seq += 1
            continue
        snapshots = _sub_breadcrumbs(section, start, primary, section_start)
        for sub in split_text(section, cfg):
            out.append(Chunk(sub.content, start + sub.start,
                             start + sub.end, seq,
                             _breadcrumb_at(snapshots, sub.start, breadcrumb)))
            seq += 1
    return coalesce_tiny(out, cfg.chunk_size)