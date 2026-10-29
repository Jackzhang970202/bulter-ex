# -*- coding: utf-8 -*-
"""Language-aware token approximation (ported from ``chunker/tokens.go``)."""
import re
import unicodedata

LANG_EN = "en"
LANG_DE = "de"
LANG_ZH = "zh"
LANG_MIXED = "mixed"

CHARS_PER_TOKEN = {LANG_EN: 4.0, LANG_DE: 4.5, LANG_ZH: 1.7, LANG_MIXED: 3.0}

_GERMAN_STOPWORDS = (" der ", " die ", " das ", " und ", " ist ", " nicht ",
                     " mit ", " auf ")
_UMLAUTS = set("äöüÄÖÜß")


def _is_cjk(ch):
    name = unicodedata.name(ch, "")
    return name.startswith(("CJK UNIFIED", "HANGUL", "HIRAGANA", "KATAKANA"))


def detect_language(text):
    if not text:
        return LANG_MIXED
    cjk = latin = umlaut = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
        elif ch in _UMLAUTS:
            umlaut += 1
            latin += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1
    total = cjk + latin
    if not total:
        return LANG_MIXED
    cjk_ratio = cjk / total
    latin_ratio = latin / total
    if cjk_ratio >= 0.15 and latin_ratio >= 0.15:
        return LANG_MIXED
    if cjk_ratio > 0.3:
        return LANG_ZH
    if umlaut or any(w in text[:512].lower() for w in _GERMAN_STOPWORDS):
        return LANG_DE
    return LANG_EN


def approx_tokens(text, lang=LANG_MIXED):
    return approx_tokens_from_len(len(text or ""), lang)


def approx_tokens_from_len(length, lang=LANG_MIXED):
    if length <= 0:
        return 0
    ratio = CHARS_PER_TOKEN.get(lang, CHARS_PER_TOKEN[LANG_MIXED])
    value = length / ratio
    return 1 if value < 1 else int(value + 0.5)


def chars_for_token_limit(tokens, lang=LANG_MIXED):
    if tokens <= 0:
        return 0
    ratio = CHARS_PER_TOKEN.get(lang, CHARS_PER_TOKEN[LANG_MIXED])
    return int(tokens * ratio * 0.9)


def split_sentences(text):
    """Sentence-ish units used for fine-grained fallback splitting."""
    return [part for part in re.split(r"(?<=[。！？；.!?;\n])", text) if part]