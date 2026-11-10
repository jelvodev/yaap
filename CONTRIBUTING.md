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

## Packages and base images

The images install only what is pinned, so a build today gets the same packages as a build last month.

### Python packages

`backend/requirements.txt` lists YAAP's direct packages; `docker/sandbox-helper/requirements.txt` those of the sandbox helper. Next to each is a `requirements.lock` with every package, also the indirect ones, and the hashes of its files for Linux, macOS and Windows. The images install the lock with `pip install --require-hashes --no-deps`, so a package that is not in the lock, or a file whose hash differs, stops the build. Local development keeps `requirements-dev.txt`, without hashes.

To add, remove or update a package, change its line in `requirements.txt` and remake the lock in the same folder with [uv](https://docs.astral.sh/uv/):

```bash
cd backend   # or docker/sandbox-helper
uv pip compile --universal --python-version 3.14 --generate-hashes requirements.txt -o requirements.lock
```

Commit both files. The helper's packages stay at the versions YAAP itself uses. Running the same command without a change in `requirements.txt` keeps the versions in the lock; add `--upgrade-package <name>` to move an indirect package to its newest version.

### Base images

Every `FROM` in `docker/` and the `docker-proxy` image in `docker-compose.yml` name a tag and a digest (`python:3.14-slim@sha256:…`). Docker uses the digest; the tag is there for reading, so a new image under the same tag changes nothing until you update the digest on purpose:

```bash
docker buildx imagetools inspect python:3.14-slim
```

Take the `Digest:` at the top of the output (the index for all platforms, not one of the platform manifests below it), replace the old digest everywhere the image is named, build the images and commit with what changed in the new image.

### Audit

`scripts/audit.sh` (Linux, macOS, Git Bash) or `scripts/audit.ps1` (Windows) check the pinned packages against the published vulnerability databases. They need npm and uv.

```bash
./scripts/audit.sh
powershell -ExecutionPolicy Bypass -File scripts\audit.ps1
```

They check the frontend (`npm audit` on `frontend/package-lock.json`), both Python lock files (`pip-audit`), and the npm packages installed globally in the images (Codex, Playwright MCP, pyright, the TypeScript language server and TypeScript), at the versions their `ARG` in the Dockerfile names. Each check is listed as `ok` or `FAILED` at the end, and the exit code is non-zero when any check has a finding or could not run. The global packages have no lock file in the images, so their dependencies are resolved as a build today would. Debian's own packages in the images are not checked.

## Releasing

A release is an annotated tag (`v0.6.0`) plus a section in [CHANGELOG.md](CHANGELOG.md); there are no GitHub Releases. While you work, add each user-facing change to the `## Unreleased` section of the changelog, and write under `### Action needed` what a person must do themselves when updating (it says "None." when nothing).

To release, check out `main` (clean, equal to `origin/main`) and run the script with the new version:

```bash
./scripts/release.sh 0.6.0
powershell -ExecutionPolicy Bypass -File scripts\release.ps1 0.6.0
```

The script refuses a version that is not newer, sets it everywhere it lives (`backend/app/version.py`, `backend/pyproject.toml`, `frontend/package.json` and its lock file, `frontend/openapi.json` and the line in the roadmap) through `backend/app/scripts/set_version.py`, turns `Unreleased` into `## 0.6.0 (date)` with a fresh empty one above it, commits "Set the version to 0.6.0", and creates the tag with the changelog section as its message. It asks before it runs `git push origin main v0.6.0`.

`GET /api/v1/version` answers with the running version. A build can also name the commit and the channel it comes from, with the environment variables `YAAP_COMMIT` and `YAAP_CHANNEL` (build arguments of the image); both are empty for a plain build.

## Pull requests

- One subject per pull request, with tests for new behaviour.
- Commit messages have a short subject in the imperative, in sentence case ("Add a public roadmap").
- Describe what changes for the user and how you checked it.
- Keep [docs/architecture.md](docs/architecture.md) and the user documentation in step with the change.
