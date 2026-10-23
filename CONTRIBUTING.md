# Contributing

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # fill in model endpoint and key
python run.py
```

The frontend is plain HTML/CSS/JS with no build step. Open `http://127.0.0.1:8633`.

## Conventions

- Python: PEP 8; keep modules and functions single-purpose; read new configuration from environment variables.
- Frontend: reuse existing design tokens and component classes; all user-facing copy is Chinese (the product targets Chinese-speaking departments).
- Never put real keys or real business data in code, tests, or documentation.
- Before committing, run:
  ```bash
  python -m compileall -q knowlens scripts tests
  python -m unittest discover -s tests -v
  node --check web/app.js
  ```

## Commits and pull requests

- Use Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`.
- One concern per pull request. Include screenshots for UI changes.
- New features need reproducible verification steps; changes to retrieval or evolution logic must describe their impact.

## License

By contributing you agree that your contributions are licensed under the MIT License.