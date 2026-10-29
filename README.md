# KnowLens 知鉴

**Answers with evidence. Knowledge that evolves.**

KnowLens is a department-level knowledge Q&A and experience-evolution agent. It turns documents, images, and meeting recordings into searchable, citable, verifiable knowledge, and improves itself through a human-reviewed loop: **candidate → review → rule / profile → release**. It ships with **193 catalogued capabilities**, a knowledge cockpit, an agent platform (agents, 1,137 installable open-source skills, experts, teams, workflows, automation) and optional Claude Code experience integration.

English | [简体中文](README.zh-CN.md)

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Version](https://img.shields.io/badge/version-0.3.0-green.svg)

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
| In-space traceable assistant | Ask about the current library or document, or select any text/number to ask where it came from. Provenance cards show the source document/page/table/figure, formulas and inputs, and jump back to highlighted source text. |
| URL ingestion | Paste a public page URL, preview the extracted Markdown, tag it, and index it into a library. |
| Multimodal ingestion | PDF (text / scanned / mixed), Word, PPT, Excel, Markdown, images, and audio recordings. |
| Image understanding | Each image is transcribed and semantically described, then inserted into the document in reading order and made searchable. |
| Audio pipeline | ffmpeg normalization → ASR → timestamped transcript → structured meeting minutes (agenda / decisions / actions / terms). |
| Experience evolution | Candidate extraction, three-level deduplication, human review, rules / profiles, space-level release digest, versioning, rollback, and evidence replay. |
| Cockpit | Knowledge health, answer verdicts, ingestion trends, library health, risks, and model usage. |
| Claude Code integration (optional) | Import development sessions as experience candidates and inject the release digest into a project's SessionStart hook. Disabled by default. |


## Agent platform (0.3.0)

The knowledge core is also an agent platform: agents, skills, experts, teams,
workflows and automation live in the same process, database and interface.

| Capability | Description |
|---|---|
| Agents | Author persona, model, temperature, step budget, knowledge scope, skills and tool permissions; publish with version snapshots and roll back. |
| Unified workspace | One composer for knowledge, agents, experts, teams, workflows and Excel Q&A; attachments, structured run forms and traces in one view. |
| AI creation | Describe a goal; the platform drafts a validated workflow, agent or team with clarifying questions; nothing is published automatically. |
| Share & artifacts | Read-only conversation links, standalone artifact pages, and agent version diffs. |
| External runtime | Optional zdata-agent-starter sidecar: discovers its agents/teams/workflows and runs them through the same chat (`ZDATA_BASE_URL`). |
| Excel 问数 | Upload xlsx/xls/csv, profile sheets/columns, ask in natural language; generated pandas runs in an AST-checked subprocess; answers include result tables and optional charts. |
| Skill marketplace | **1,137 open-source skills** harvested from four MIT/upstream repositories (metadata-only catalog); install on demand, search by category/licence, import from URL, author prompt/HTTP/script/MCP skills, and bundle them into first-class skill packs. |
| Experts | Publish an agent as a discoverable expert with expertise tags, rating and usage count; chat from the expert profile. |
| Teams | Round-table, supervisor-routed and handoff strategies over multiple agents, with a synthesizer and bounded turns. |
| Workflows | Typed DAGs (LLM / agent / skill / tool / knowledge / condition / parallel / approval / delay), JSON editor, live DAG preview, run trace and approval resume. |
| MCP registry | Browse the official MCP server registry, discover tools, and install a remote server as an executable skill. |
| Tools | Allow-listed registry: knowledge search, calculator, JSON query, time, HTTP (SSRF-guarded), file read, web search. Every call is recorded, including depth-limited agent-to-agent delegation. |
| Memory and evolution | Session history, user/agent/space/global memory, candidate extraction from corrections and failed runs, human approval, and injection into prompts. |
| Evaluation | Per-agent evaluation sets with keyword checks and version-tied scores. |
| Automation | Five-field cron schedules for agents and workflows, run-now, next-run calculation and run history. |
| Operations | Header-based users with admin/editor/viewer roles, publication review, append-only audit log, artifacts and usage overview. |
| Command palette | `Ctrl/Cmd+K` searches documents, agents, skills, experts, teams, workflows, sessions and memories. |
| Bilingual UI | All interface strings live in `web/i18n.js`; toggle Chinese/English in Settings without rebuilding. |

The platform layer is additive: existing routes and tables are untouched, and
`KNOWLENS_PLATFORM=0` restores the pure 0.2.0 behaviour.

## Architecture

```
Browser (vanilla JS + ECharts, no build step)
   │ REST / SSE
FastAPI service (one process, one port)
   ├─ Knowledge: Q&A routing → PageIndex tool loop → citation verification
   ├─ Ingestion: PDF / Office / images / audio → hierarchical tree → index
   ├─ Experience: candidates → review → rules / profiles → digest → injection
   ├─ Platform: agents / skills / experts / teams / workflows / tools / memory
   └─ Storage: SQLite (WAL) + files (index, originals, OCR cache, artifacts)
```

```
knowlens/            Backend package (FastAPI, Q&A, ingestion, evolution, vision, audio)
knowlens/platform/   Agent platform (agents, skills, runtime, tools, workflows, ops)
catalog/             Harvested open-source skill catalog (1,137 entries, metadata only)
web/                 Frontend (index.html / app.js / platform.js / styles / vendor)
vendor/pageindex/    Embedded PageIndex retrieval engine (MIT)
scripts/             Startup, skill harvester, digest injection, release build
design/              Five-round design record and feature catalog
tests/               Model-free unit and API tests
docs/                Architecture, release guide, user guide, operations runbook, screenshots
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
8. **Ask inside the knowledge space** — open the right-side assistant from a library or document, or select text/number in the document to ask "where does this number come from". Answers include a provenance card; click "open source" to jump to the page and highlight the excerpt. Panel conversations persist by scope.

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
| GET/POST | `/api/platform/agents` | Agent CRUD, publish, versions, run (SSE) |
| GET | `/api/platform/skills/catalog` | Search the 1,137-skill open catalog |
| POST | `/api/platform/skills/install` | Install a catalog skill |
| GET/POST | `/api/platform/experts`, `/teams` | Experts and teams, run (SSE) |
| GET/POST | `/api/platform/workflows` | Typed DAGs, validate, run (SSE) |
| GET | `/api/platform/runs/{id}` | Run trace, steps and artifacts |
| GET/POST | `/api/platform/schedules` | Cron automation |
| GET | `/api/platform/search?q=` | Cross-kind search for the command palette |
| GET | `/api/platform/overview` | Platform metrics and 14-day trend |

## Development and tests

```bash
python -m compileall -q knowlens scripts tests
python -m unittest discover -s tests -v     # 58 tests: core, platform API, units
node --check web/app.js
node --check web/platform.js
node --check web/i18n.js
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
- Vector recall for the skill marketplace
- Human correction of image-understanding results written back to the index
- More cockpit metrics and alerts
- Deferred: SSO/JWT, PostgreSQL, Milvus, drag-and-drop workflow canvas

Release notes: [`docs/release-notes-0.3.0.md`](docs/release-notes-0.3.0.md).

User guide (Chinese): [`docs/user-guide.zh-CN.md`](docs/user-guide.zh-CN.md) — task-oriented walkthrough of every workspace.

Operations runbook: [`docs/platform-operations.md`](docs/platform-operations.md) — start/stop, model channels, backup, RBAC, skills, MCP, scheduling, troubleshooting.

## License and third parties

This project is licensed under the [MIT License](LICENSE).

- PageIndex: MIT (vendored under `vendor/pageindex/`, license included)
- ECharts: Apache-2.0 (`web/vendor/echarts.min.js`)
- marked: MIT (`web/vendor/marked.min.js`)
- pdfplumber: MIT; pypdfium2: Apache-2.0 / BSD-3-Clause; PDFium third-party notices ship with the dependency
- PyMuPDF: AGPL-3.0 or commercial; **not installed by default** — evaluate its license before enabling it
- python-docx / python-pptx / openpyxl / FastAPI / LiteLLM follow their own licenses