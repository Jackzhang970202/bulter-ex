# -*- coding: utf-8 -*-
"""Global configuration for 知鉴 KnowLens.

All keys and endpoints can be provided via environment variables or the .env file in the project root; no keys live in the code.
The primary channel is an OpenAI-compatible endpoint; the fallback channel is an Anthropic-compatible endpoint (probed for fallback at startup).
"""
import os

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"), override=False)

# ── Brand ──
APP_NAME = "知鉴"
APP_NAME_EN = "KnowLens"
VERSION = "0.1.0"
TAGLINE = "有出处的回答，会进化的知识"

# ── Paths ──
DATA = os.environ.get("KNOWLENS_HOME") or os.environ.get(
    "DEPTKB_HOME") or os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA, "kb.db")
SPACES = os.path.join(DATA, "spaces")
UPLOADS = os.path.join(DATA, "uploads")
ARCHIVE = os.path.join(DATA, "archive")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


# ── Model channels (keys come from environment variables only) ──
MIMO = {
    "protocol": "openai",
    "model": _env("LLM_MODEL", "mimo-v2.6-pro"),
    "api_key": _env("LLM_API_KEY"),
    "base_url": _env("LLM_BASE_URL"),
}
MAAS = {
    "protocol": "anthropic",
    "model": _env("FALLBACK_MODEL",
                  os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL",
                                 "qwen3.8-flash[1m]")),
    "api_key": _env("FALLBACK_API_KEY"),
    "base_url": _env("FALLBACK_BASE_URL"),
}

HOST = _env("KNOWLENS_HOST", "127.0.0.1")
PORT = int(_env("KNOWLENS_PORT", "8633"))
DIGEST_MAX_BYTES = 20480      # Space digest size ceiling
LIB_DESC_MAX = 400            # Per-document description truncation

# Q&A behavior
MAX_TURNS = 14                # Max rounds of the agent retrieval loop
SUFFICIENCY_ROUNDS = 2        # Max evidence supplementation rounds

# Image understanding (vision)
VISION_ENABLED_DEFAULT = True
OCR_CACHE_DIR = os.path.join(DATA, "ocr_cache")
PDF_RENDER_SCALE = 2.5        # Scanned page render scale (about 180 DPI)
VISION_MAX_SIDE = 2000        # Max longest edge of an image before sending it to the model
VISION_MIN_SIDE = 80          # Images with a side smaller than this are treated as decorative and skipped
VISION_MIN_AREA_RATIO = 0.012 # Skip images smaller than 1.2% of the page area
OCR_CONCURRENCY = 3           # Per-document image recognition concurrency (reserved)
SCAN_TEXT_THRESHOLD = 40      # Below this average chars-per-page the document is treated as scanned
PDF_PAGE_GROUP = 10           # Tree node grouping every 10 pages for scanned/image PDFs