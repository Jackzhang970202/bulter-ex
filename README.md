# KnowLens 知鉴

**Answers with evidence. Knowledge that evolves.**

KnowLens is a department-level knowledge Q&A and experience-evolution agent. It turns documents, images, and meeting recordings into searchable, citable, verifiable knowledge, and improves itself through a human-reviewed loop: **candidate → review → rule / profile → release**. It ships with a knowledge cockpit and optional Claude Code experience integration.

English | [简体中文](README.zh-CN.md)

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Version](https://img.shields.io/badge/version-0.1.0-green.svg)

## Screenshots

| Cockpit | Knowledge spaces |
|---|---|
| ![Cockpit](docs/images/dashboard.png) | ![Knowledge spaces](docs/images/knowledge.png) |

| Q&A | Experience center |
|---|---|
| ![Q&A](docs/images/chat.png) | ![Experience center](docs/images/experience.png) |

## Features

| Capability | Description |
|---|---|
| Space / library isolation | A space is the isolation boundary. Multiple libraries inside one space can be queried jointly; cross-space queries are impossible. |
| Five-stage Q&A | Scope routing → page-level evidence gathering → sufficiency check → structured answer → citation verification. |
| Verifiable citations | Citations point to document pages and can expand to the original text; invalid citations are removed. |
| Two chat surfaces | Knowledge-base chat and temporary-material chat, plus a mini Q&A dock on each library page. |
| Multimodal ingestion | PDF (text / scanned / mixed), Word, PPT, Excel, Markdown, images, and audio recordings. |
| Image understanding | Each image is transcribed and semantically described, then inserted into the document in reading order and made searchable. |
| Audio pipeline | ffmpeg normalization → ASR → timestamped transcript → structured meeting minutes (agenda / decisions / actions / terms). |
| Experience evolution | Candidate extraction, three-level deduplication, human review, rules / profiles, space-level release digest, versioning, rollback, and evidence replay. |
| Cockpit | Knowledge health, answer verdicts, ingestion trends, library health, risks, and model usage. |
| Claude Code integration (optional) | Import development sessions as experience candidates and inject the release digest into a project's SessionStart hook. Disabled by default. |

## Architecture

```
Browser (vanilla JS + ECharts)
   │ REST / SSE
FastAPI service
   ├─ Q&A pipeline: routing → PageIndex tool loop → citation verification
   ├─ Ingestion: PDF / Office / images / audio → hierarchical tree → index
   ├─ Experience: candidates → review → rules / profiles → digest → injection
   └─ Storage: SQLite (WAL) + files (index, originals, OCR cache)
```

```
knowlens/            Backend package (FastAPI, Q&A, ingestion, evolution, vision, audio)
web/                 Frontend (index.html / app.js / theme and component styles / vendor)
vendor/pageindex/    Embedded PageIndex retrieval engine (MIT)
scripts/             Startup, digest injection, release build
tests/               Model-free core tests
docs/                Architecture, release guide, screenshots
data/                Runtime data (gitignored)
```

## Quick start

Requirements: Python 3.10+; ffmpeg optional (for audio transcription).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # fill in model endpoint and key
python run.py                      # open http://127.0.0.1:8633
```

On Windows you can also run `scripts\start.bat`.

### Docker

```bash
cp .env.example .env               # fill in model settings
docker compose up -d --build
# http://127.0.0.1:8633, data persisted in ./data
```

The service binds to `127.0.0.1` by default. For LAN access, set `KNOWLENS_HOST=0.0.0.0` and add authentication and TLS at the reverse proxy.

## Configuration

All configuration is supplied through environment variables or a local `.env` file. No keys are hardcoded.

| Variable | Default | Description |
|---|---|---|
| `LLM_MODEL` | `mimo-v2.6-pro` | Primary chat model (OpenAI-compatible) |
| `LLM_API_KEY` | empty | Primary model key |
| `LLM_BASE_URL` | empty | Primary model endpoint (OpenAI-compatible `/v1`) |
| `FALLBACK_MODEL` / `FALLBACK_API_KEY` / `FALLBACK_BASE_URL` | empty | Optional Anthropic-compatible fallback channel |
| `KNOWLENS_HOST` | `127.0.0.1` | Bind address |
| `KNOWLENS_PORT` | `8633` | Port |
| `KNOWLENS_HOME` | `./data` | Data directory |
| `KNOWLENS_CC_ROOT` | `~/.claude/projects` | Claude Code transcript root |
| `KNOWLENS_PDF_BACKEND` | auto | Set to `pdfplumber` to force the permissive PDF backend |

## Usage

1. **Create a space** — the isolation boundary for all knowledge.
2. **Create libraries** — retrieval units inside a space; multiple can be queried jointly.
3. **Upload** — multiple files, a whole folder (recursive), or drag and drop. Choose tags and whether to enable image understanding.
4. **Inspect content** — the document drawer offers page-by-page browsing, a structure tree, tag editing, and an image-understanding list (preview, transcription, description, per-image re-OCR).
5. **Ask** — select libraries and tags in the composer. Answers have four sections: conclusion (yes / no / conditional / not covered), evidence with citations, method or improvement (model inference is labelled), and missing material. Expand **Intermediate process** to inspect routing, tool calls, and human-readable raw data.
6. **Experience center** — review candidates with evidence replay, maintain rules, inspect and roll back releases, and view distillation history. Optionally configure Claude Code experience.
7. **Cockpit** — monitor knowledge health, answer quality, ingestion trends, risks, and model usage.

## Multimodal behavior

| Input | Processing | Citation page |
|---|---|---|
| Standalone image | Vision model transcribes text and describes semantics; stored as a document | Page 1 |
| Scanned / image-only PDF | Detected automatically; each page is rendered and OCR'd | Original PDF page |
| Text PDF with embedded images | Text and image blocks are assembled in reading order (including a two-column heuristic); images are understood individually | Original PDF page |
| Word | Paragraphs, tables, and images extracted in body XML order | Pseudo page |
| PPT | Shapes ordered by slide position; images understood individually | Slide number |
| Audio | ffmpeg + ASR + structured minutes; stored as a document | Pseudo page |

Image-understanding results are written into the body as `[Image understanding · page N · figure M (type)] transcription / description`, so they are searchable and citable. OCR results are cached by image SHA-256; re-indexing does not call the model again. A failed image is marked and shown as "partial image failure" without blocking the rest of the document.

PDF parsing uses **pdfplumber + pypdfium2** by default (permissive licenses). If PyMuPDF is installed, it is used as a faster backend; its AGPL license is the user's responsibility.

## Evolution and Claude Code

- **Sources**: document ingestion (including meeting minutes), Q&A corrections, and development sessions.
- **Deduplication**: approved/rejected synonymous candidates are silent; a candidate synonymous with an active rule increments the rule's correction count; pending synonyms merge evidence.
- **Human review**: candidates only enter the Q&A prompt after approval; the model never rewrites rules on its own.
- **Release digest**: injected into every Q&A call, versioned, rollback-able, and rejected above 20 KB (the previous version is kept).
- **Claude Code capture (optional)**: add a code project path in the experience center; KnowLens reads the matching sessions under `~/.claude/projects`, masks sensitive data, and distils candidates.
- **Claude Code injection (optional)**: writes a SessionStart hook into the project's `.claude/settings.local.json`, backs up the original file, and can be removed at any time. The hook exits silently when the service is down.

## Data and security

- No telemetry by default; data stays in the local data directory.
- Uploaded content and questions are sent to the model endpoint you configure. Follow your organization's data-compliance rules.
- Keys are read only from environment variables / `.env` and never committed.
- Backup: stop the service and copy the entire data directory.
- Vulnerability reports: use GitHub Security Advisories; see [SECURITY.md](SECURITY.md).

## API overview

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Service and model status |
| GET | `/api/dashboard?space_id=` | Cockpit aggregates |
| GET/POST | `/api/spaces` | Spaces |
| GET/POST | `/api/spaces/{id}/libraries` | Libraries |
| POST | `/api/documents/upload` | Upload (files / folder, `vision=1/0`) |
| GET | `/api/documents/{id}/pages/{n}` | Page text |
| GET | `/api/documents/{id}/images` | Image-understanding records |
| POST | `/api/qa/stream` | Q&A (SSE) |
| POST | `/api/feedback` | Accept / correct an answer |
| GET/POST | `/api/candidates/...` | Candidate review and evidence |
| GET/POST | `/api/spaces/{id}/digest` | Release digest |
| GET/POST | `/api/spaces/{id}/cc/...` | Claude Code capture and injection |

## Development and tests

```bash
python -m compileall -q knowlens scripts tests
python -m unittest discover -s tests -v
node --check web/app.js
```

CI runs these checks on GitHub Actions without calling a real model.

## Release rules

- The repository contains only source code, frontend, vendored dependencies, scripts, tests, and documentation.
- **Never commit**: `data/`, `.env`, `_probe/`, logs, caches, `.exe`, or any other build artifacts.
- The release archive is produced by `python scripts/build_release.py` (`dist/knowlens-<version>.zip`). The build rejects `.exe` files and scans for leaked keys.
- `.github/workflows/release.yml` builds and attaches the archive when a `v*` tag is pushed. Binaries and deployment packages live in GitHub Releases, never in git history.

## Roadmap

- Hybrid lexical / vector recall for very large libraries
- Space export and import
- Multi-user support and authentication
- Human correction of image-understanding results written back to the index
- More cockpit metrics and alerts

## License and third parties

This project is licensed under the [MIT License](LICENSE).

- PageIndex: MIT (vendored under `vendor/pageindex/`, license included)
- ECharts: Apache-2.0 (`web/vendor/echarts.min.js`)
- marked: MIT (`web/vendor/marked.min.js`)
- pdfplumber: MIT; pypdfium2: Apache-2.0 / BSD-3-Clause; PDFium third-party notices ship with the dependency
- PyMuPDF: AGPL-3.0 or commercial; **not installed by default** — evaluate its license before enabling it
- python-docx / python-pptx / openpyxl / FastAPI / LiteLLM follow their own licenses