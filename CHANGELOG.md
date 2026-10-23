# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and Semantic Versioning.

## [Unreleased]

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