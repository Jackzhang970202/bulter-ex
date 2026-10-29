# -*- coding: utf-8 -*-
"""Multilingual boundary patterns (ported from ``chunker/patterns.go``)."""
import re

PRIO_FORM_FEED = 100
PRIO_NUMBERED_HEAD = 90
PRIO_CHAPTER_MARKER = 85
PRIO_ALL_CAPS_HEADING = 70
PRIO_VISUAL_SEP = 60
PRIO_PAGE_FOOTER = 50
PRIO_BLANK_BLOCK = 40

MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FORM_FEED = re.compile(r"\f")
NUMBERED_SECTION = re.compile(
    r"^[ \t]*(?:\d+(?:\.\d+){1,3}\.?|(?:\d+|[IVX]{1,5})\.)[ \t]+\S.{0,200}$")
ALL_CAPS_HEADING = re.compile(
    r"^[ \t]*([A-ZÄÖÜ][A-ZÄÖÜ \-]{3,80}):?\s*$")
VISUAL_SEPARATOR = re.compile(
    r"^[ \t]*(?:-{3,}|={3,}|\*{3,}|_{3,})[ \t]*$")
EXCESSIVE_BLANKS = re.compile(r"\n{3,}")
PAGE_FOOTER = re.compile(
    r"^[ \t]*(?:Seite|Page|页码?)\s+\d+(?:\s*(?:von|of|/)\s*\d+)?[ \t]*$",
    re.IGNORECASE)
GERMAN_CHAPTER = re.compile(
    r"^[ \t]*(?:Kapitel|Abschnitt|Teil)\s+(?:[0-9]+|[IVX]{1,5})[.: ].{0,200}$")
ENGLISH_CHAPTER = re.compile(
    r"^[ \t]*(?:Chapter|Section|Part)\s+(?:[0-9]+|[IVX]{1,5})[.: ].{0,200}$")
CHINESE_CHAPTER = re.compile(
    r"^[ \t]*第[ \t]*[一二三四五六七八九十百千零〇0-9]+[ \t]*(?:章|节|節|部分|篇)[ \t]?.{0,200}$")

# Content that must never be split.
PROTECTED = (
    re.compile(r"\$\$.*?\$\$", re.S),
    re.compile(r"!\[[^\]]*\]\([^)]+\)"),
    re.compile(r"\[[^\]]*\]\([^)]+\)"),
    re.compile(r"[ ]*(?:\|[^|\n]*)+\\|\r?\n\s*(?:\|\s*:?-{3,}:?\s*)+\|?\r?\n"),
    re.compile(r"[ ]*(?:\|[^|\n]*)+\\|\r?\n"),
    re.compile(r"```(?:\w+)?[\r\n].*?```", re.S),
    re.compile(r"`[^`\r\n]+`"),
)

# Markdown table header (row + separator row) used for context propagation.
TABLE_HEADER = re.compile(
    r"^\s*(?:\|[^|\n]*)+\r?\n\s*(?:\|\s*:?-{3,}:?\s*)+\|?\r?\n", re.M | re.S)
TABLE_ROW = re.compile(r"^\s*(?:\|[^|\n]*)+\|\s*$", re.M)


def sentence_separators(lang):
    if lang == "zh":
        return ["。", "！", "？", "；", "\n"]
    if lang in ("de", "en"):
        return [". ", "! ", "? ", "; ", "\n"]
    return ["。", "！", "？", "；", ". ", "! ", "? ", "; ", "\n"]


def chapter_patterns(langs):
    mapping = {"de": GERMAN_CHAPTER, "en": ENGLISH_CHAPTER,
               "zh": CHINESE_CHAPTER}
    if not langs:
        return [GERMAN_CHAPTER, ENGLISH_CHAPTER, CHINESE_CHAPTER]
    out = [mapping[lang] for lang in langs if lang in mapping]
    return out or [GERMAN_CHAPTER, ENGLISH_CHAPTER, CHINESE_CHAPTER]