# Architecture

## Layers

| Layer | Location | Responsibility |
|---|---|---|
| Frontend | `web/` | Single-page app: cockpit, knowledge spaces, Q&A, experience center, settings. Vanilla JS + ECharts, no build step. |
| Service | `knowlens/server.py` | FastAPI: REST, SSE, static assets, aggregates. |
| Q&A | `knowlens/qa.py` | Scope routing, PageIndex tool loop, sufficiency check, citation verification. |
| Ingestion | `knowlens/ingest.py` | Job queue, format parsing, tree building, image records, evolution hooks. |
| Multimodal | `pdfproc.py`, `officeproc.py`, `vision.py`, `audio.py` | Layout order, scanned-page OCR, Office images, audio transcription. |
| Experience | `evolution.py`, `cc.py`, `privacy.py` | Candidates, deduplication, rules, profiles, digest, Claude Code, masking. |
| Storage | `db.py` + data directory | SQLite (WAL) + files (originals, index trees, OCR cache). |
| Retrieval | `vendor/pageindex/` | Embedded PageIndex: hierarchical index and page-level evidence. |

## Data model

- Space → library → document → tag: `spaces / libraries / documents / tags / doc_tags`
- Image understanding: `doc_images`
- Conversations: `sessions / messages / qa_events / adhoc_files`
- Evolution: `candidates / rules / profiles / digests / distill_runs / filter_rules`
- Claude Code (optional): `cc_projects / cc_sessions`
- Observability: `events` (progress), `usage` (model calls)

## Isolation and retrieval

- Space isolation: each space has its own index directory and PageIndex client. The server derives `space_id` from the session; the client cannot override it.
- Library isolation: a library is a retrieval allowlist. Libraries that are not selected never enter the candidate pool or the tool-layer `doc_id` allowlist.
- Joint retrieval: multiple libraries inside one space can be selected; candidate documents are merged, routed, and read in one tool loop.
- Citation verification: `<cite>` markers are parsed, documents and page numbers validated, original excerpts attached, and invalid citations removed.

## Ingestion and multimodal

- Non-PDF: parsed to Markdown → heading-based tree → pseudo pages (200 lines each) → DocStore.
- PDF: pdfplumber + pypdfium2 by default. Scanned pages are OCR'd in full; page numbers match the original PDF. Mixed pages are assembled in text/image block reading order.
- Images: the vision model returns type, verbatim transcription, and semantic description; the result is inserted in order and recorded in `doc_images`.
- Audio: ffmpeg normalization → ASR → timestamped transcript → structured minutes → ingestion.
- Batch: at most two documents are indexed concurrently; progress is pushed through `events`.

## Experience evolution

```
Documents / meetings / Q&A corrections / development sessions
        │
        ▼
Candidates (three-level deduplication, evidence index)
        │ human review
        ▼
Rules / profiles / glossary
        │ render
        ▼
Space release digest (versioned, 20 KB guard, rollback)
        │
        ├─ Q&A system prompt
        └─ Claude Code SessionStart hook (optional)
```

## Security boundaries

- Keys come only from environment variables / `.env`.
- The service binds to localhost by default. Public deployment requires authentication and TLS at the reverse proxy.
- Development sessions are masked before import. Injection modifies only the selected project's local settings, with an automatic backup and an uninstall path.