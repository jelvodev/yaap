# YAAP

YAAP (Your AI Agent Platform) is a personal, self-hosted, model-agnostic AI development environment. How it is built is in `docs/architecture.md`; read it before making changes.

- Write documentation, code, comments, API fields and configuration descriptions in English.
- Backend: Python (FastAPI). Frontend: TypeScript (React + Vite).
- Keep the core vendor-neutral. Vendor-specific code belongs only in `backend/app/providers/`.
- Never commit secrets or populated environment files; document variables in `.env.example`.

## Code standards

These standards apply to every change: new code follows them, and code that a change touches moves toward them.
When a rule cannot be followed, say why in the commit message.

### Structure

- **Thin HTTP layer.** Routers in `backend/app/api/` parse the request, call a service and translate its result and
  domain errors to responses. Logic lives in `core/`, `agent/` and the feature packages. The only other HTTP
  endpoints are the MCP and A2A protocol endpoints (`backend/app/a2a/server.py`, `backend/app/agent/mcp_server.py`,
  `backend/app/agent/outside_mcp.py`) and the route in `backend/app/main.py` that serves the built frontend. They
  and the API token dependency in `backend/app/auth/` may raise `HTTPException` as `api/` does; nothing else does.
  Nothing outside `api/` imports from `app.api` (schemas included). Every other entry point (goal mode, the
  scheduler, A2A) calls the same service, never a router or a fake request.
- **One place per concern.** Before writing a helper, search for an existing one and reuse or extend it. Concerns
  with a single owner: the environment for subprocesses (`safe_subprocess_env()`, never `os.environ` as a whole),
  choosing provider, model and key (`resolve_provider()`), and the permission decision (a pure function). No second
  copy of a list, check or formatter, also not in the frontend.
- **Vendor knowledge only in `backend/app/providers/`**: default models, connection options, status checks and
  session formats come from the provider's spec, not from settings, routers or feature code.
- **Size.** A function longer than about 60 lines or a module over about 600 lines is split before more is added to
  it. Split by responsibility (a service, a step, a hook), not into `utils`.
- **Explicit dependencies.** Services are typed (a dataclass or a Protocol), never `Any`. No unchecked `**kwargs`
  constructors. New shared state goes into a typed service object, not as a new loose attribute on `app.state`.
  Avoid import cycles instead of hiding them in function-local imports.

### Code

- **Types.** Every function is annotated. Status and kind fields are `Literal` or an `Enum` end to end, so schemas
  need no `type: ignore`. TypeScript stays `strict` without `any`, `@ts-ignore` or non-null assertions on data that
  can be absent.
- **Errors.** Catch the exceptions you expect. A broad `except Exception` only at a boundary that must keep running
  (the agent loop, a background task, a plugin), with the error logged or shown, and a comment saying why.
- **Suppressions** (`noqa`, `type: ignore`) name the rule and give the reason on the same line.
- **Comments** explain why, not history: no plan or milestone numbers, dates of changes or former names.
- **Security decisions** (permissions, secrets, paths, tokens) are pure functions with table-driven tests. Values
  of secrets never reach logs, tool results, subprocess environments they are not meant for, or git.
- **Format.** Python and TypeScript: double quotes, at most 120 columns, imports grouped standard library, third
  party, own code. Keep CRLF line endings in files that have them.

### Frontend

- A page component composes; state and effects per concern go into hooks (`useConversationStream` and the like).
  Don't add state to a component that is already large; extract first.
- All API calls go through the typed client in `src/api/`; no raw `fetch` with unchecked casts. Event and message
  shapes shared with the backend come from generated or shared types, not hand-written copies.

### Tests and migrations

- Tests are named after the behaviour they check. Decision logic gets table-driven tests (`pytest.mark.parametrize`).
- Every model change has a migration, and the test suite checks that the migrated schema matches the models.
- Parallel work: keep edits to shared files (`backend/app/main.py`, `backend/app/storage/models.py`,
  `backend/app/core/settings.py`, `backend/app/agent/tools.py`, `frontend/src/pages/ChatPage.tsx`) small and local,
  and put new code in its own module.
