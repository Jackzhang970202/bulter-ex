# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and Semantic Versioning.

## [Unreleased]

### Added
- **Knowledge Hub port, wave 1–3**: adaptive chunker (profiler / heading / heuristic / recursive, parent-child, token limit, preview), parser registry (pypdfium2 PDF with page markers, DOCX/PPTX/XLSX/CSV/HTML/EPUB/MHTML/images/audio, optional MarkItDown), unified ingestion into `documents`/`chunks`/`document_versions`, unified retrieval (FTS + LIKE fallback + validity filter + parent expansion + optional vector with RRF and rerank), and `/api/kbs` CRUD/upload/search/reparse endpoints.
- **Sensitive gate and duplicate detection**: local rule set (credentials, PII, classification marks, custom terms) with pass/redact/review/block decisions and mask-based redaction; MinHash + Winnowing duplicate detection with canonical relations, coverage/jaccard evidence and review status.
- **FAQ, manual and URL imports**: FAQ-type knowledge bases with standard/similar/negative questions, answer strategies, JSON/CSV import-export and similarity search; manual Markdown and URL ingestion share the parsing/chunking pipeline; tree mode now emits nested section chunks.
- **Model configuration UI**: a dedicated page to create/edit/test/delete LLM, embedding, rerank, vision and speech model sources.
- **Knowledge enrichment**: per-document summary (stored on the document and as a `summary` chunk), generated questions indexed as retrieval aliases, auto-tagging from existing KB tags with a confidence threshold, and deterministic table summaries for CSV/Excel; wired into ingestion and runnable on demand from the UI.
- **Knowledge governance**: 90-day recycle bin (soft delete / restore / purge), chunk editing with optimistic locking and revision history (rollback rebuilds the FTS index), batch operations (reparse / delete / validity / tags / move), KB-scoped tag CRUD with force delete, and folder aggregation.
- **Single knowledge entry**: the legacy “知识空间” navigation item is removed; classic libraries are materialised into the unified store on startup and managed from the “知识库” page. The old “分块知识库” entry is now that single “知识库” page backed by `/api/kbs` (list, documents, chunks, search test, settings, versions, upload with parse-mode/chunk overrides); startup runs the idempotent migration so classic libraries and kb2 bases appear in one place.
- **Configurable model sources**: `model_configs` table and `/api/platform/models` for LLM / Embedding / Rerank / VLM / ASR (provider, base URL, API key, model name, default flag, connectivity test); keys are stored server-side and only returned masked.
- **Model type alignment and capability fields**: kinds renamed to `chat` / `embedding` / `rerank` / `vllm` / `asr` (legacy `llm`/`vlm` auto-migrated), plus `dimension`, `context_window`, `supports_vision`, `source` and custom headers.
- **Model configuration and debug UI**: type tabs with counts, model cards with capability chips and default/status, an editor drawer with source/provider/header fields, and a debug drawer showing connectivity, latency, metrics, response preview and error details (modeled on the reference product's debug drawer).
- **Parser engine registry**: `/api/platform/parsers` exposes builtin/simple/markitdown plus optional MinerU, PaddleOCR-VL, OpenDataLoader and anydoc engines with availability and reasons; the KB settings page maps 11 file-type groups to engines and supports Excel first-row headers.
- **Full-page KB settings**: nine grouped sections (basic, models, parsing/chunking, parsers, indexing, governance, enrichment, advanced, danger) replacing the former drawer, with ranges, defaults and help text.
- **KB detail layout**: compact stat strip, pill tabs, table-style document rows with a search filter and a `⋯` action menu; header actions consolidated into add-document dropdown and primary upload.
- **Upload confirmation and progress**: tabbed confirmation (tags / parsing / chunking / advanced) with per-batch overrides, folder upload with relative paths, SHA-256 duplicate pre-check with skip/allow/ask, per-file status, weighted progress and automatic parse-status polling.
- **Bug fixes**: all upload form fields (`parse_mode`, `parse_config`, `tags`, validity, relative paths) were previously read as query parameters and silently ignored — they are now proper `Form` fields; `GET /api/kbs/{id}` returned the raw library row (string config) instead of the parsed object, which double-encoded configuration on save.

### Added
- **Unified knowledge store (P1, additive)**: `libraries`/`documents` gain the fields a single storage needs, and a new `chunks` table (page/section/text in one place) plus version, tag, folder, recycle-bin, fingerprint and sensitive-finding tables. `python -m knowlens.kbstore.migrate` materialises existing `kb2_*` data and PageIndex pages/trees into it without re-parsing or calling a model; it is idempotent and rebuilds the FTS index. No existing read/write path or UI is switched yet.

### Added
- **Chunk-level knowledge base (kb2)**: SQLite + FTS5 (trigram) storage with heading-aware 512/80 chunking, background ingestion, page mapping, chunk preview and search; parses PDF, Word, PowerPoint, Excel, CSV (UTF-8/GBK/UTF-16), images (OCR), HTML, Markdown, text, JSON and source code.
- **Knowledge bases attach to any module**: agents, experts, teams, workflows, Excel Q&A and the classic knowledge chat all accept `kb_ids`; a `知识库` picker lives in the composer and persists the choice on the object.
- **Adaptive KB + Excel answering**: the planner detects data-driven vs policy-driven questions, runs table analysis and knowledge retrieval separately, then merges them into one answer with citations; when the table cannot answer it falls back to the knowledge base and says so.
- Classic and chunk-level knowledge bases can be switched from each other's toolbar.
- **Knowledge governance for chunk bases**: content-hash duplicate rejection, document tags, per-document enable/disable, per-chunk enable/disable, re-index with new chunk settings, and base settings (rename, description, chunk size/overlap).
- **Document versions and validity**: upload a new revision (previous file is kept), list versions, roll back (rollback itself becomes a new version); documents carry a validity window and stop matching after expiry.
- **Number-aware retrieval**: Chinese and Arabic numerals are unified (`500元` matches `五百元`) and mismatched figures no longer cross-match (`八百元` no longer returns a `五百元` chunk).

### Fixed
- Excel analysis results containing Chinese were mojibake (`python -I` ignores `PYTHONIOENCODING`, so the child process wrote GBK); the child now pins UTF-8 and `-X utf8`.
- Long questions returned no knowledge hits because 3-char windows were truncated in order; terms are now sampled across the whole question with a two-character LIKE fallback ranked by overlap.
- CSV/HTML/JSON uploads to the chunk base failed because the shared extension mapper does not know them.

## [0.3.0] - 2026-10-26

### Added
- **Agent platform**, merged into the same process, database and UI as the knowledge core: agents, skills, experts, teams, workflows, tools, memory, evaluation, automation, governance and observability.
- **Open skill marketplace with 1,137 skills** harvested from `davila7/claude-code-templates`, `wshobson/agents`, `anthropics/skills` and `obra/superpowers`; metadata catalog plus on-demand install, provenance and licence badges, URL import, and custom prompt/HTTP/script/MCP skills.
- Agent runtime with a provider-neutral LLM client (OpenAI-compatible and Anthropic-compatible), streaming tool calls, bounded ReAct loop, knowledge prefetch, skill injection, memory injection and per-step traces.
- Teams with round-table, supervisor and handoff strategies; workflows with typed DAG nodes, condition branching, parallel fan-out, human approval and resume.
- Allow-listed tools (knowledge search, calculator, JSON query, time, HTTP with SSRF guard, file read, web search) with server-side permission checks and audit rows.
- Layered memory (user/agent/space/global) with candidate extraction from corrections and failed runs, human approval and prompt injection.
- Evaluation sets per agent with keyword scoring tied to the published version.
- Cron automation for agents and workflows, run-now, next-run calculation and run history.
- Editor/Admin operations: `X-User-Id` identity, admin/editor/viewer roles, review queue, append-only audit log, artifacts and usage overview.
- Installed skill files (SKILL.md, scripts, references) can be previewed from the skill detail drawer.
- Skill install now downloads the full skill directory (SKILL.md, scripts, references) with a manifest and cleans it up on uninstall; falls back to SKILL.md only if upstream is unreachable.
- Skill packs as first-class objects (create, edit, apply to an agent), skill catalog refresh, and persisted failure notifications with a read/unread inbox.
- Bulk document tagging across a multi-select in the knowledge base, no-code agent templates, team templates, workflow JSON import/export, expert usage stats, platform JSON export, Q&A CSV export, and 90-day run retention.
- Live-verified HTTP skills (httpbin.org) and MCP skills (initialize + tools/call over streamable HTTP against mcp.deepwiki.com); session ids are isolated per object and user.
- URL ingestion with main-content extraction (trafilatura when present, stdlib fallback), preview, tags and the existing indexing pipeline.
- Official MCP registry browser with tool discovery, one-click install and manual URL entry; agent-to-agent delegation (`call_agent`) with a depth guard.
- Centralised bilingual UI dictionary (`web/i18n.js`) with a live Chinese/English switch in Settings; business content keeps its source language.
- Command palette (`Ctrl/Cmd+K`) across documents, agents, skills, experts, teams, workflows, sessions and memories.
- Seven new UI views and a shared trace console; charts now follow theme changes (fixes the stale dark-mode chart colors).

### Changed
- UI system v2: target selector moved inside the composer as a text button with an anchored, searchable menu; per-target drafts and focus retention; no full re-render on switch.
- Homepage composer is centered and docks to the bottom after the first message; sidebar regrouped into Knowledge / Build / Operate with recent targets and `Ctrl/Cmd+B` collapse.
- Attachment menu (upload/paste), drag-and-drop, message copy/regenerate, stop button, and a collapsible trace drawer.
- Recent targets can be starred; artifacts preview inside the trace drawer; mobile sidebar becomes an overlay drawer; `/` focuses the composer.
- Simplified knowledge composer: mode/scope/upload moved into the `+` menu, anchored popovers with measured heights and line icons; attachment upload is discoverable again.
- AI creation: describe a goal and get a validated draft workflow, agent or team (max three clarifying questions, never auto-published).
- Workflow condition nodes now require both branches; AI drafts are retried once with the validation error.
- Workflow runs highlight the current node on the flow canvas; team runs show a live turn timeline with handoff reasons.
- AI editing of existing workflows/agents/teams: open the same conversational designer with the current object loaded and save as an update.
- Full-page conversational designer: chat on the left, live draft/preview on the right, per-turn change list, save as draft; workflow JSON moved into an advanced section.
- Read-only layered flow canvas (branches, approvals, parallel) and team collaboration graph inside the explanation drawer.
- Workflow/team plain-language explanations (steps, branches, approvals, roles, strategy), collapsible recent targets, and a documented AI-creation design.
- Share links (`/share/<token>`) with read-only snapshots, standalone artifact pages (`/artifact/<id>`), and agent version diff.
- Optional zdata-agent-starter federation: discover external agents/teams/workflows and run them through the same chat with normalized SSE and run traces (`ZDATA_BASE_URL`).
- Composer-level skills and tools: per-run skill/tool overlays for agents, experts, teams and knowledge Q&A; two-pane target dropdown; knowledge composer `+` menu for uploads, URL ingestion and scope selection; `/` command palette with new chat, target/skill/tool pickers and Markdown export.

### Changed
- Model ids are normalised by stripping client hints such as `[1m]`; providers reject them (`qwen3.8-flash[1m]` → 404, `qwen3.8-flash` → 200).
- Version bumped to 0.3.0. Platform tables are additive (`pf_*`); existing routes and data are untouched. `KNOWLENS_PLATFORM=0` disables the platform layer.
- Test suite expanded from 5 to 58 tests (unit + FastAPI API tests with a stub model).

### Changed
- Unified chat workspace: agents, experts, teams, workflows and Excel 问数 are selectable from one composer; attachments, workflow input schemas, run traces and approvals live in the same view.
- Visual redesign: flat neutral surfaces, light sidebar, one accent color, a single target pill with a searchable hub, 760px conversation column and a collapsible trace panel.
- Excel 问数: xlsx/xls/csv profiling, tuned prompts adapted from AskBI, generated pandas executed in an AST-checked subprocess rather than `exec`.

### Fixed
- Security review fixes: high-risk tools are now gated on every execution path (teams, workflows, evaluations, delegation), skill import validates URLs against the SSRF guard, URL ingestion validates each redirect hop before fetching, and cyclic workflows are rejected at validation instead of running to the step limit.
- Supervisor teams no longer loop forever when a member agent was deleted; deleting an agent now detaches it from teams and removes its version snapshots.
- Standard cron semantics for day-of-month/day-of-week (OR when both are restricted), fallback channel keeps its own model instead of the agent's, rejected memories can be proposed again, and `temperature=0` is preserved.
- Dashboard trend chart used a hidden second y-axis, making bars incomparable with the line; both series now share one integer axis.
- Top-right theme toggle did not re-render the active view.

### Deferred
- SSO/JWT, PostgreSQL, MinIO, Milvus/vector recall, arbitrary-code sandbox, drag-and-drop workflow canvas.

## [Unreleased]

### Added
- In-space traceable Q&A assistant: ask about the current library or document; select any text/number to ask where it came from; provenance cards show source page/table/figure plus formulas and input sources; jump to the source page and highlight the excerpt; panel sessions persist by scope.
- Audio ASR progress now reports a percentage based on audio duration (with a reliable WAV-frame duration probe) instead of a raw transcribed-second count.

### Planned
- Hybrid lexical / vector recall for very large libraries
- Space export and import
- Multi-user support and authentication
- Human correction of image-understanding results written back to the index

## [0.1.0] - 2026-10-22

### Added
- Brand and unified interface: KnowLens; cockpit design (light glass and midnight dark); one navigation shell with five modules.
- Cockpit: knowledge health, answer verdicts, ingestion trends, library health, risks, recent sessions/documents, model usage.
- Experience center: candidate evidence replay, rule hit/correction counts, three-level deduplication, distillation history, masking rules.
- Optional Claude Code integration: import project sessions and distil candidates; inject the release digest through a SessionStart hook (automatic backup, removable, disabled by default).
- Human-readable intermediate process: tool-call summaries with collapsible raw data; citations, image descriptions, and release digests rendered as Markdown.
- Standard project layout: `knowlens/` package, `tests/`, `docs/`, `pyproject.toml`, Docker, CI, and a release build script.
- License and dependency compliance: MIT; PDF parsing defaults to pdfplumber + pypdfium2; PyMuPDF is optional (AGPL, not installed by default).
- Multimodal ingestion: PDF (text / scanned / mixed), Word, PPT, Excel, Markdown, images, and audio recordings.
- Self-evolution: candidates, human review, rules / profiles, space-level release digest, versioning, and rollback.
- Local-first operation: keys via environment variables; data stays on the machine.