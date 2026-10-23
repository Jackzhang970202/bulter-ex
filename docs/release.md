# Release process

## Versioning

- Semantic Versioning: `MAJOR.MINOR.PATCH`. Keep the version in `knowlens/config.py` and `pyproject.toml` in sync.
- Record changes in `CHANGELOG.md` (Keep a Changelog format).

## Repository rules

Only source code, frontend assets, vendored dependencies, scripts, tests, and documentation are committed.

Never commit:

- `data/` (knowledge, indexes, uploads, OCR cache, model cache)
- `.env` (keys)
- `_probe/` (test materials and temporary scripts)
- logs, `*.bak`, `__pycache__`
- `.exe`, zip archives, container images, or any other build artifact

Keys and deployment packages are distributed through the local environment and GitHub Releases only; they never enter git history.

## Build the release archive

```bash
python scripts/build_release.py
```

Output: `dist/knowlens-<version>.zip`. The script:

1. copies only runtime code, frontend, `vendor/pageindex`, scripts, tests, and docs;
2. excludes `data/`, `_probe/`, `.env`, logs, and caches;
3. rejects `.exe` files;
4. scans text files for leaked keys and aborts on a match.

## Publishing

1. Update `CHANGELOG.md` and the version number.
2. Commit and tag: `git tag v0.1.0 && git push origin v0.1.0`.
3. The `release.yml` workflow builds the zip and attaches it to the GitHub Release.
4. Do not commit screenshots containing real business data. Use minimal, masked demo data.

## Deployment

- Source: `pip install -r requirements.txt && python run.py`
- Docker: `docker compose up -d --build` (data mounted at `./data`)
- When exposing the service, add authentication and TLS at the reverse proxy and restrict source networks.