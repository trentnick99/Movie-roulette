# Development

Requirements: Python 3.11+, Node.js 20+, FFmpeg.

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt     # Windows: .venv\Scripts\pip
cd frontend && npm ci && cd ..
scripts/dev.sh        # backend on :8765 with reload + Vite on :5173 (open http://localhost:5173)
```

On Windows without bash, run in two terminals:

```
cd backend && ..\.venv\Scripts\python -m app --no-browser
cd frontend && npm run dev
```

## Tests

| Command | What |
|---|---|
| `cd backend && ../.venv/bin/python -m pytest` | backend: scanning, parsing, matching, TMDB client, categories, selection fairness, timestamps, game flow over HTTP and WebSocket, scoring, spoiler protection, security, settings, backups, migrations, real FFmpeg clips (skipped without FFmpeg) |
| `cd backend && ../.venv/bin/ruff check app tests` | lint |
| `cd frontend && npm test` | Vitest + React Testing Library |
| `cd frontend && npm run lint && npm run typecheck && npm run build` | ESLint, TypeScript, production build |
| `scripts/e2e.sh` | Playwright: a complete game night in a real browser against a real server, real FFmpeg, generated test videos and a fake TMDB server, including a phone joining |

TMDB is always mocked in tests (`backend/tests/fake_tmdb.py`); no key is needed. `scripts/e2e.sh` needs
a browser that can decode H.264: Google Chrome (`MR_E2E_CHANNEL=chrome`) or a Chromium build with codecs
(`CHROMIUM_PATH`). Playwright's own Chromium download cannot play the clips.

## Database changes

1. Edit `backend/app/models.py`.
2. Generate a migration:
   ```bash
   cd backend
   ../.venv/bin/python -c "from app.migrate import alembic_config; from alembic import command; \
     command.revision(alembic_config('/tmp/scratch.db'), message='describe change', autogenerate=True)"
   ```
   (upgrade `/tmp/scratch.db` to head first with `command.upgrade(..., 'head')` so the diff is only your change).
3. Review the file in `backend/alembic/versions/`. `tests/test_security_system.py::test_migrations_match_models`
   fails if models and migrations disagree.

## Releases

Push a tag `vX.Y.Z`. The release workflow builds the interface and attaches
`MovieRoulette-vX.Y.Z-windows.zip` (source + pre-built interface) to a GitHub release. Bump `__version__` in
`backend/app/__init__.py` and `version` in `frontend/package.json` first.

## Packaging

A single-file desktop build (PyInstaller + bundled FFmpeg) is a possible later step; the browser-based app does
not depend on it.
