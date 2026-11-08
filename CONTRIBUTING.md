# Contributing

Thank you for helping. YAAP is a personal project, so larger changes are best discussed in an issue first.

## Read the design first

[docs/architecture.md](docs/architecture.md) is the map of the code: how YAAP is put together and where each part lives. A change that alters behaviour updates it and the user documentation in the same commit.

## Conventions

- **English** for code, comments, documentation, API fields, setting descriptions and commit messages.
- **Backend:** Python 3.12 or later, FastAPI, SQLAlchemy with Alembic. **Frontend:** TypeScript, React and Vite.
- **Vendor-neutral core.** Code for a specific vendor (a model provider, a secret store) belongs only in `backend/app/providers/`.
- **No secrets in git.** Never commit `.env`, keys, tokens or a data folder. New environment variables are documented in `.env.example` and [docs/configuration.md](docs/configuration.md).
- **Every database change is a migration** in `backend/app/storage/migrations/versions/`, applied automatically on start.
- **Tests without tokens.** `FakeProvider` replays recorded responses, so tests never call a real model.
- **Code standards.** Structure, typing, errors, security decisions, the frontend and tests follow the code standards in [AGENTS.md](AGENTS.md#code-standards).

## Development setup

Backend (Python 3.12 or later):

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/python
YAAP_MASTER_KEY=<key> YAAP_DATA_DIR=../data YAAP_INTERNAL_URL=http://127.0.0.1:8000 .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Frontend, with the Vite dev server proxying `/api` to port 8000:

```bash
cd frontend
npm install
npm run dev
```

Some features need tools that the Docker images provide (Claude Code, Codex, Tesseract, the embedding model; Playwright and Chromium in the sandbox image). Run the whole application with `docker compose up -d --build` to try them. Outside Docker there is no runner, so set `YAAP_RUNNER=off` to let the agent's commands run in your backend process (with everything that process can read); the test suite does the same.

## Checks

```bash
cd backend && .venv/Scripts/python -m pytest
cd frontend && npm run typecheck
```

After a change to the API, regenerate the frontend types:

```bash
cd backend && .venv/Scripts/python -m app.scripts.export_openapi ../frontend/openapi.json
cd frontend && npm run gen:api
```

## Pull requests

- One subject per pull request, with tests for new behaviour.
- Describe what changes for the user and how you checked it.
- Keep the design and the user documentation in step with the change.
