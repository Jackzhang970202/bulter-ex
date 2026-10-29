# -*- coding: utf-8 -*-
"""Document profiler and tier selection (ported from ``profiler.go``)."""
import math
from dataclasses import dataclass, field

from . import patterns
from .tokens import LANG_EN, LANG_DE, LANG_MIXED, LANG_ZH, detect_language

TIER_HEADING = "heading"
TIER_HEURISTIC = "heuristic"
TIER_LEGACY = "legacy"


@dataclass
class DocProfile:
    total_chars: int = 0
    total_lines: int = 0
    avg_line_len: float = 0.0
    std_line_len: float = 0.0
    md_heading_counts: dict = field(default_factory=dict)
    md_heading_total: int = 0
    numbered_section_count: int = 0
    all_caps_short_line_count: int = 0
    blank_paragraph_breaks: int = 0
    form_feed_count: int = 0
    visual_sep_count: int = 0
    german_chapter_count: int = 0
    english_chapter_count: int = 0
    chinese_chapter_count: int = 0
    repeated_footer_count: int = 0
    has_tables: bool = False
    has_code: bool = False
    code_ratio: float = 0.0
    detected_langs: list = field(default_factory=list)

    def heading_density(self):
        return self.md_heading_total / self.total_lines if self.total_lines else 0.0

    def dominant_heading_level(self):
        if not self.md_heading_total:
            return 0
        for level in range(1, 7):
            if self.md_heading_counts.get(level, 0) >= 3:
                return level
        for level in range(6, 0, -1):
            if self.md_heading_counts.get(level, 0) > 0:
                return level
        return 0

    def heuristic_marker_total(self):
        return (self.numbered_section_count + self.german_chapter_count
                + self.english_chapter_count + self.chinese_chapter_count
                + self.all_caps_short_line_count + self.visual_sep_count
                + self.form_feed_count)


def profile_document(text):
    profile = DocProfile()
    if not text:
        return profile
    profile.total_chars = len(text)
    profile.form_feed_count = text.count("\f")
    lines = text.split("\n")
    profile.total_lines = len(lines)

    lengths, code_chars, in_fence = [], 0, False
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("```"):
            in_fence = not in_fence
            profile.has_code = True
            continue
        if in_fence:
            code_chars += len(line)
            continue
        lengths.append(len(line))
        match = patterns.MARKDOWN_HEADING.match(line)
        if match:
            level = len(match.group(1))
            profile.md_heading_counts[level] = \
                profile.md_heading_counts.get(level, 0) + 1
            profile.md_heading_total += 1
            continue
        if patterns.NUMBERED_SECTION.match(line):
            profile.numbered_section_count += 1
        if patterns.GERMAN_CHAPTER.match(line):
            profile.german_chapter_count += 1
        if patterns.ENGLISH_CHAPTER.match(line):
            profile.english_chapter_count += 1
        if patterns.CHINESE_CHAPTER.match(line):
            profile.chinese_chapter_count += 1
        if patterns.ALL_CAPS_HEADING.match(line):
            profile.all_caps_short_line_count += 1
        if patterns.VISUAL_SEPARATOR.match(line):
            profile.visual_sep_count += 1
        if patterns.PAGE_FOOTER.match(line):
            profile.repeated_footer_count += 1
        if trimmed.startswith("|") and trimmed.endswith("|"):
            profile.has_tables = True

    if lengths:
        profile.avg_line_len = sum(lengths) / len(lengths)
        variance = sum((l - profile.avg_line_len) ** 2 for l in lengths) / len(lengths)
        profile.std_line_len = math.sqrt(variance)
    if profile.total_chars:
        profile.code_ratio = code_chars / profile.total_chars
    profile.blank_paragraph_breaks = text.count("\n\n\n")

    lang = detect_language(text[:4096])
    profile.detected_langs = ([LANG_EN, LANG_DE, LANG_ZH]
                              if lang == LANG_MIXED else [lang])
    return profile


def select_strategy(profile):
    chain = []
    if (profile and profile.md_heading_total >= 3
            and profile.heading_density() > 0.005
            and profile.dominant_heading_level() > 0):
        chain.append(TIER_HEADING)
    if (profile and (profile.heuristic_marker_total() >= 5
                     or profile.form_feed_count > 0
                     or profile.german_chapter_count
                     + profile.english_chapter_count
                     + profile.chinese_chapter_count > 0)):
        chain.append(TIER_HEURISTIC)
    chain.append(TIER_LEGACY)
    return chain