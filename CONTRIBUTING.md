# Contributing

Thank you for helping. YAAP is a personal project, so larger changes are best discussed in an issue first.

## Read the architecture first

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

Backend (Python 3.12 or later). On Linux and macOS use `.venv/bin/python` wherever these commands say `.venv/Scripts/python`.

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
YAAP_MASTER_KEY=<key> YAAP_DATA_DIR=../data YAAP_RUNNER=off YAAP_INTERNAL_URL=http://127.0.0.1:8000 .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Frontend, with the Vite dev server proxying `/api` to port 8000:

```bash
cd frontend
npm install
npm run dev
```

Some features need tools that the Docker images provide (Claude Code, Codex, Tesseract, the embedding model; Playwright and Chromium in the sandbox image). Run the whole application with `docker compose up -d --build` to try them. Outside Docker there is no runner, so the command above sets `YAAP_RUNNER=off`: the agent's commands then run in your backend process (with everything that process can read). The test suite does the same.

## Checks

```bash
cd backend && .venv/Scripts/python -m pytest
cd frontend && npm run typecheck && npm run test:unit && npm run test:sdk && npm run build
```

`npm run build` also generates the plugin SDK from `openapi.json` and type-checks it.

Backend tests are in `backend/tests/` (`test_<subject>.py`). Frontend unit tests are `*.test.mjs` files next to the code they test; the plugin SDK's tests are in `frontend/plugin-sdk/`.

After a change to the API, regenerate the frontend types:

```bash
cd backend && .venv/Scripts/python -m app.scripts.export_openapi ../frontend/openapi.json
cd frontend && npm run gen:api
```

## Pull requests

- One subject per pull request, with tests for new behaviour.
- Commit messages have a short subject in the imperative, in sentence case ("Add a public roadmap").
- Describe what changes for the user and how you checked it.
- Keep [docs/architecture.md](docs/architecture.md) and the user documentation in step with the change.
