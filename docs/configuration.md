# Configuring YAAP

Almost everything is set in the interface, under **Settings**. Only the master key and a few container details live in `.env` and `docker-compose.yml`.

## Environment variables

Set in `.env` next to `docker-compose.yml`. `.env.example` lists them; the setup script fills in the master key.

| Variable | Needed | Meaning |
|---|---|---|
| `YAAP_MASTER_KEY` | yes | Encrypts every key and secret YAAP stores (a Fernet key: 32 random bytes as URL-safe base64). Keep a copy outside the data folder. YAAP refuses to start without a valid one. |
| `YAAP_MASTER_KEY_FILE` | no | Instead of `YAAP_MASTER_KEY`: the path inside the container of a file that holds the key, for example a Docker secret. See [The master key](#the-master-key). |
| `YAAP_MASTER_KEY_PREVIOUS` | no | Only while changing the master key: the old key. See [The master key](#the-master-key). |
| `YAAP_ALLOWED_HOSTS` | no | Extra host names YAAP answers on besides `localhost`, `127.0.0.1` and `[::1]`, separated by commas or spaces, such as `yaap.lan`. Empty by default; see [Addresses YAAP answers on](#addresses-yaap-answers-on). |
| `YAAP_PREVIEW_PORT` | no | The port of the preview beside the chat, where a dev server the agent started is shown. Default `8081`, published on `127.0.0.1` only like YAAP's port; `0` switches the preview off. See [The preview port](#the-preview-port). |
| `YAAP_DATA_HOST` | no | The folder on your computer that holds YAAP's data. Default `./data`. |
| `YAAP_SANDBOX_CPUS` | no | CPUs per runner and per project sandbox. Default `2`. |
| `YAAP_SANDBOX_MEMORY` | no | Memory per runner and per project sandbox, such as `4g` (the default) or `512m`. |
| `YAAP_SANDBOX_IMAGE` | no | The image runners and sandboxes start from. Default `yaap-sandbox:local`; extra packages are better set as below. |

Set by `docker-compose.yml`, the image or a built-in default; change them only when you know why:

| Variable | Meaning |
|---|---|
| `YAAP_SEARCH_URL` | Where YAAP reaches SearXNG for the agent's `web_search` tool (`http://searxng:8080`). Without it, the tool is not offered. |
| `YAAP_DATA_DIR` | The data folder inside the container (`/data`). |
| `YAAP_STATIC_DIR` | The built frontend inside the image. |
| `YAAP_INTERNAL_URL` | How the agent's tools reach YAAP inside the container (default `http://127.0.0.1:8080`). |
| `YAAP_EMBEDDING_MODEL_DIR` | Where the embedding model is, instead of `data/models/bge-m3/<revision>/`; for development. |
| `YAAP_SANDBOX_URL` | Where YAAP reaches the sandbox helper (`http://yaap-sandbox:8000`), which starts the runners and sandboxes. Without an answer from it, the agent's commands are refused. |
| `YAAP_RUNNER` | `on` (the default): commands, MCP servers over stdio and the browser run in the environment's runner. `off`: they run in YAAP's own process, with access to everything YAAP can read. Only for development without Docker (and the test suite); never in the Docker installation. |
| `YAAP_SANDBOX_TOKEN_FILE` | The helper's token, which the helper writes into a volume both mount (`/run/yaap-sandbox/token`). |
| `YAAP_PREVIEW_BIND` | The address the preview listener binds inside YAAP's process: `0.0.0.0` in the image (Docker publishes it on `127.0.0.1` only), `127.0.0.1` by default outside Docker. Outside Docker the preview is off unless `YAAP_PREVIEW_PORT` is set. |

The sandbox helper (`yaap-sandbox`) reads `DOCKER_HOST` (the socket proxy), `YAAP_SANDBOX_IMAGE`
(`yaap-sandbox:local`), `YAAP_SANDBOX_CPUS` and `YAAP_SANDBOX_MEMORY`. It finds the Compose project name and where
the workspace is on your computer from its own container; `YAAP_SANDBOX_INSTANCE` and `YAAP_SANDBOX_WORKSPACE_HOST`
set them by hand when that does not work. `YAAP_SANDBOX_PIDS` limits the number of processes per container (default 2048).

Backup destinations of kind Folder live under `YAAP_BACKUP_ROOT` (default `/backup`), where `docker-compose.override.yml` mounts a folder of your computer (see [Backups](#backups)).

### Addresses YAAP answers on

YAAP answers only when the browser asks for it by `localhost`, `127.0.0.1` or `[::1]` (on any port); a request for
any other host name gets 400. This stops a web page from reaching YAAP by pointing a name of its own at your computer
(DNS rebinding). A request that a browser sends from a page must also come from one of those hosts: a page on another
site gets 403 when it tries to change something, open a stream or a WebSocket, or read YAAP's answers. Programs that
send no `Origin` (curl, scripts with an API token, Claude Code and Codex calling YAAP's tools on
`http://127.0.0.1:8080/mcp`) are not affected, and neither is the frontend's development server on
`localhost:5173`.

To open YAAP under another name, for example `http://yaap.lan:8080` through your own DNS or `hosts` file, add the name
to `.env` and restart YAAP (`docker compose up -d`):

```ini
YAAP_ALLOWED_HOSTS=yaap.lan
```

`docker-compose.yml` passes the value to the `yaap` service; several names are separated by commas or spaces. A port in an entry is ignored; a malformed entry stops YAAP at start with its name. Adding a name does not make YAAP
safe to expose on a network (see [SECURITY.md](../SECURITY.md)).

### The preview port

The preview panel beside the chat shows the app of a dev server the agent started with `start_process` and a port. It
comes from a second listener in YAAP on `YAAP_PREVIEW_PORT` (default `8081`), so the app has an origin of its own and
its pages can reach nothing of YAAP's (see [SECURITY.md](../SECURITY.md)). The preview answers on the same host
names as YAAP: open YAAP on `http://localhost:8080` and the preview is `http://localhost:8081`.

To use another port, set it in `.env` (`YAAP_PREVIEW_PORT=8091`) and run `docker compose up -d`; `docker-compose.yml`
publishes the same port on `127.0.0.1`. To switch the preview off, set `YAAP_PREVIEW_PORT=0` and replace the `yaap`
service's ports in `docker-compose.override.yml`, because Docker cannot publish port 0:

```yaml
services:
  yaap:
    ports: !override ["127.0.0.1:8080:8080"]
```

The preview reaches a dev server in the runner or a project's sandbox over YAAP's SSH connection to it; the sandbox
image allows that connection to forward to `localhost` only. After updating YAAP, rebuild the sandbox image
(`docker compose build sandbox-image`) and let the runners start again, or the preview cannot reach their dev servers.

## Extra packages for the runner

The runner of each environment and every project sandbox start from the sandbox image `yaap-sandbox:local` (Debian with build tools, Python 3, Node.js, git and Chromium). The agent may install more with `sudo` while it works, but that lasts only until the runner is made again (after an update of the image). Packages every runner should always have go into one place: an extra layer on the sandbox image, named in `docker-compose.override.yml` as build arguments of the service `sandbox-image`. YAAP's own image does not change for them.

```yaml
services:
  sandbox-image:
    build:
      args:
        # Debian packages, separated by spaces.
        EXTRA_APT_PACKAGES: "postgresql-client default-jdk-headless"
        # Any further setup, run once as root while the image is built.
        EXTRA_SETUP: "npm install --global pnpm@10 && pip install --break-system-packages uv"
```

Then run `docker compose up -d --build`. Every runner and sandbox is made again from the new image on its next use, so what the agent installed by hand outside the project folders is gone; the project folders stay. The commented example in `docker-compose.override.example.yml` shows the same. MCP servers over stdio run in the runner too, so a server started with `uvx` or another tool needs that tool here.

Language servers for the code tools (`find_definition`, `find_references`, `diagnostics`, `rename_symbol`, `document_symbols`) run in the runner or sandbox too. The image has pyright (Python) and typescript-language-server (TypeScript and JavaScript). YAAP also knows gopls (Go), rust-analyzer (Rust) and clangd (C and C++); install them here and YAAP finds them the first time it starts a language server in a runner, from the next conversation on:

```yaml
services:
  sandbox-image:
    build:
      args:
        EXTRA_APT_PACKAGES: "gopls clangd"
        EXTRA_SETUP: "curl -fsSL https://github.com/rust-lang/rust-analyzer/releases/download/2025-01-06/rust-analyzer-x86_64-unknown-linux-gnu.gz | gunzip > /usr/local/bin/rust-analyzer && chmod 0755 /usr/local/bin/rust-analyzer"
```

## Levels and inheritance

YAAP is organised in levels:

```
You (global)
├── Work (environment)
│   ├── Project A
│   └── conversations outside a project
└── Private (environment)
    └── Project B
```

- **Global** settings apply everywhere. An **environment** can override them, and a **project** can override its environment. The nearest level wins: project, environment, global, then the default.
- Every setting shows where its value comes from ("Inherited from Global", "Overridden in Work") and offers a reset.
- **Keys, the GitHub token, secrets and projects belong to one environment.** Keeping billing and data apart is what environments are for. A conversation never receives keys, standards or memory of another environment.
- The **active environment** is chosen in the header; its colour marks the interface.

Settings has a level switch at the top of its rail. Project settings are the last icon of the project rail in the chat (also the gear next to a project).

## Connections and models

**Settings › Connections › Providers**, at Global and overridable per environment; API keys are added per environment:

- **Claude connection**: *Claude Code* (your subscription, signed in once with `docker compose exec -it yaap claude auth login`) or *Anthropic API key*.
- **OpenAI connection**: *API key* or *Codex* (your ChatGPT plan, signed in once with `docker compose exec -it yaap codex login --device-auth`).
- **API keys** for Anthropic and OpenAI. The **GitHub token** (a fine-grained personal access token, limited to the repositories YAAP may use) is added in Settings › Git and GitHub. Keys are tested before they are stored, stored encrypted, and never shown again: only the last four characters.
- **Ollama address** (at the Global level), for example `http://host.docker.internal:11434` for Ollama on this computer. Its models then appear in every model list, without cost.
- **OpenAI-compatible endpoints** (`openai_endpoints`, at the Global level): servers that speak the OpenAI Chat Completions API, such as LM Studio or vLLM, as a list of `{name, base_url, local}`. The name is a lowercase slug, unique, and not the name of a provider of YAAP; the base URL is an http(s) address including `/v1`. Each endpoint is a provider named like it, with the models from `GET {base_url}/models` as `<name>/<model id>`, read at start, after a change and with *Refresh models*. The context window and prices come from the listing when it has them (OpenRouter does, as `context_length` and `pricing`); otherwise the context is 32k and the model has no price, so it is not metered and budgets do not apply to it. `local` says the server runs on your own computer or network, so the model policy's "only local models" allows it. An endpoint is its own vendor for the model policy (connection `<name>:endpoint`) and for reviews by another vendor; that check sees the endpoint, not the company that runs the model behind it, so a model reached through OpenRouter can come from the same company as the one it reviews. Agents use YAAP's tool loop; a model that rejects tools gives an error naming it. Keys are not sent yet, so a server that needs one (such as OpenRouter) does not work yet.

**Settings › Models › Presets**: model presets per environment. A preset is a named set of models by role: `main` (the conversation), `worker` (delegated tasks) and `utility` (titles, commit messages, summaries), each with an effort. One preset is the default for new conversations; a conversation can pick another, or *Custom*. *Preset for chats outside a project* (per environment) names the preset a new conversation outside a project starts with; empty is the default preset. The **utility model** setting (in Utility and workers) is the fallback for presets without one, and *Utility model per task* gives a small task a model of its own, such as a local model for titles and commit messages; the check of outside content always uses the utility model. *Longest reply* sets the preset's `max_output_tokens`: the most tokens one reply may write; empty is the model's maximum (128,000 for the Claude 5.5 models), shown as the placeholder.

The **Workers** panel in **Settings › Models › Utility and workers** lists who delegated work can go to, by the name the agent uses: each preset's worker (edited in the presets table) and the coding agents *Claude Code* (`claude_code`) and *Codex* (`codex`), with how each is reached, whether it is billed per token or on a subscription, and whether it can run. For the two CLIs you choose the model and effort here (`claude_code_worker`, default Claude Sonnet 5.5 at medium; `codex_worker`, default GPT-6.1 Sol at medium; global, overridable per environment). They need their sign-in from Settings › Connections, but not the matching connection: a conversation can run on an API key and still hand work to Claude Code.

**Settings › Models › Context** (global and environment; per project in its settings): `context_limit`, how large a conversation's context may grow, in tokens, or *The model's window* (the default). YAAP shortens old tool output and summarises against this limit; the model keeps its full window. A conversation can choose its own limit next to its preset, which wins over the settings. `auto_compact` (on by default) lets YAAP keep conversations within the limit by itself: at `trim_at` percent of the limit (default 50) it shortens old tool output, at `compact_at` percent (default 70) it summarises the earlier messages. Claude Code and Codex compact their own sessions; Ollama models are compacted like API-key models. `repo_map` (on by default) starts a project conversation with a map of the project's folders, main modules and public symbols, about 1,500 tokens, made once per commit; off, the agent looks around with its tools.

Every model list shows all models; one whose connection has no key in the active environment is marked "(no key)", and one the [model policy](#model-policy) does not allow "(not allowed)", with the policy and its level as the reason. The chat and a project's scheduled tasks apply the project's policy as well.

## Permissions

**Settings › Agent › Permissions** (global, environment, project):

| Setting | Meaning |
|---|---|
| Tool permissions | Per tool: run automatically or ask first. Writing and editing files, running commands, committing, web fetch and memory updates can each be set. Pushing always asks. |
| Commands that run without asking | One per line, such as `npm test`. A command matches with extra arguments, but never with shell syntax (`;`, `|`, `&&`, `$`, redirects, globs), so nothing can be chained to it. |
| Websites read without asking | Domains for `web_fetch` and the browser; subdomains are included. |
| Check approvals after outside content | After the agent read a web page, an attachment or a skill from GitHub, every changing action asks. When on, the utility model also marks requests that look like they follow instructions from that content. |

Per conversation, the composer has an **access** choice: *as set* (the settings above), *full* (everything runs without asking; only pushing and commands that use a secret still ask) or *read only*.

## Model policy

**Settings › Models › Policy** (environment) and the project's settings (project) hold the setting `model_policy`: which models may receive this environment's data, and what may never leave YAAP. Every part is optional; a part that is not set is open in the environment and inherited in a project.

| Part | Meaning |
|---|---|
| Connections (`connections`) | The connections that may be used, as `provider:connection`: `anthropic:api_key`, `anthropic:claude_code`, `openai:api_key`, `openai:codex`, `ollama:local`, and `<name>:endpoint` for each OpenAI-compatible endpoint. |
| Models (`models`) | A list of model ids, or `"local"` for only models on a local connection (Ollama, or an endpoint marked local). |
| Business data (`business_data`) | `true` requires a model with the catalog's mark for business data processing. The mark is off for every model until it is checked for the model and the vendor's terms (`business_data` in `app/providers/catalog.py`); local models always qualify. |
| A2A agents (`agents`) | The agents `call_agent` may reach, by name. |
| Blocked patterns (`patterns`) | Regular expressions; a match blocks the request (below). A pattern that does not compile, or that matches empty text, is refused when saved. |

```json
{"connections": ["anthropic:api_key", "ollama:local"], "business_data": true, "agents": ["research"],
 "patterns": ["\\bNL\\d{2}[A-Z]{4}\\d{10}\\b"]}
```

- **Every model request is checked**: the conversation, `delegate` (the conversation's preset, a named preset, an agent's preset), Claude Code and Codex as workers, and the utility model. A refused request says which policy refused it and which level set the rule; it never falls back to another model silently. The utility model is the one exception with a fallback: when the preset's utility model is not allowed, the `utility_model` setting's model is used if the policy allows it; otherwise the task (a title, a commit message, a summary) is skipped, and the app says so with the policy's reason: a task you asked for (a commit message, a pull request text, a rule, an eval case, a summary or /compact, a learning check, tidying the memory) answers with the reason instead of a result; a task of a reply (its title, memory suggestions, routing, the check of outside content) shows a notice under the reply; a project's background work (memory tidying, learning) shows one in the project's Memory and Learning tabs. Each skip is in the audit log as *refused*; the filter *Skipped by the policy* lists them.
- **A project only narrows.** The environment's rules and the project's both apply. Saving a project policy that would allow a connection, model or agent the environment does not, or drop its business data requirement, is refused with the reason. A project's patterns come on top of the environment's.
- **The model lists and the Workers panel** mark a model or worker the policy forbids as *not allowed*, with the reason: the chat's model and preset choices (with the project's policy), a project's scheduled tasks, the presets and the utility models in Settings, and the workers in Settings › Models › Utility and workers. When the chat's model is not allowed, the chat says so and links to Settings › Models › Policy. A reply the policy refuses is recorded in the audit log as *refused*, as delegated work and utility tasks are.
- **Outgoing checks.** Before a model request leaves YAAP (every provider, also the prompt given to Claude Code and Codex), before a tool's output goes back to the model, and before an outgoing A2A message is sent, the text is checked for the values of the secrets the environment and project know (also URL-encoded and in base64) and for the listed patterns. A hit blocks it with a message naming the secret or the pattern, never the value; nothing is redacted. `{{secret:NAME}}` placeholders always pass. A blocked reply shows as the reply's error; the blocked message stays in the conversation, so edit it or start a new conversation. Patterns are Python regular expressions run on every request; keep them simple.

## The master key

`YAAP_MASTER_KEY` encrypts every key and secret in the vault. Keep a copy outside the data folder: a backup is of no use without it. Settings › Connections › Vault at Global shows where the key comes from and whether every stored value uses it.

**As a Docker secret.** To keep the key out of `.env`, put it in a file and give it to the container as a secret in `docker-compose.override.yml`:

```yaml
services:
  yaap:
    environment:
      YAAP_MASTER_KEY_FILE: /run/secrets/yaap_master_key
    secrets:
      - yaap_master_key
secrets:
  yaap_master_key:
    file: ./master-key.txt
```

Then empty `YAAP_MASTER_KEY` in `.env`. Keep `master-key.txt` out of git and out of the data folder, and readable by its owner only (`chmod 600 master-key.txt`, owned by the user with id 1000 that YAAP runs as in the container); YAAP logs a warning at start when the file gives its group or other users any access.

YAAP removes the master key from its environment once it has read it, so nothing it starts later can pass it on; see [SECURITY.md](../SECURITY.md).

**Changing the key**, without losing anything:

1. Generate a new key (see [install.md](install.md#install-by-hand)).
2. In `.env`, move the current key to `YAAP_MASTER_KEY_PREVIOUS` and put the new one in `YAAP_MASTER_KEY`.
3. Restart: `docker compose up -d`. On start YAAP re-encrypts every stored value with the new key. It works normally meanwhile.
4. When Settings › Connections › Vault says every value uses the new key, remove `YAAP_MASTER_KEY_PREVIOUS` and restart again.

A backup made with the old key can still be restored while `YAAP_MASTER_KEY_PREVIOUS` holds it; the next start moves its values to the new key.

## Backups

**Settings › System › Backup** makes a backup every day at 03:00 (in the time zone of Settings › Account › Profile) and keeps the last seven; change the schedule (a cron expression) or switch it off there. A backup missed while YAAP was down is made once at the next start. A failed scheduled backup shows in the header.

A backup in `data/backups` is on the same disk as the data, so add a **destination** as well:

- **A folder** of your computer, ideally on another disk. Mount it at `/backup` in `docker-compose.override.yml` (copy `docker-compose.override.example.yml`), restart with `docker compose up -d`, and add a destination of kind Folder with path `/backup` (or a folder inside it).
- **A network share** (SMB, such as a NAS or a Windows share). Add the password in Settings › Connections › Vault at Global as a *Network share password*, then a destination of kind Network share with the server, the share, an optional folder, the user and that password. Encryption is required unless you switch it off for that destination.

Each destination keeps its own number of backups; YAAP removes only files named like its backups there. *Test* writes and removes a small file and shows the free space. To restore from a destination, open it and use *Copy back to restore*.

## Update check

This is the one call YAAP makes on its own to a place you did not choose. A few seconds after it starts, and then on a
schedule, YAAP asks GitHub's API for the tags of its repository (`GET api.github.com/repos/jelvodev/yaap/tags`) and
reads the `CHANGELOG.md` of the newest `vX.Y.Z` tag from `raw.githubusercontent.com`. The request carries
`User-Agent: YAAP/<version>` and nothing about you, your projects or your installation. When an environment has a GitHub
token (Settings › Git and GitHub), YAAP sends it so a private repository can be read; without one, a private repository
answers "not found" and the section says that a GitHub token is needed. Pre-releases and malformed tags are ignored.

| Setting (Settings › System › Updates, Global) | Default | Meaning |
|---|---|---|
| `update_check_schedule` | `0 9 * * *` | A cron expression in the time zone of Settings › Account › Profile, or `off`. Intervals under an hour are refused. |
| `update_source` | `jelvodev/yaap` | The GitHub repository to read, as `owner/repository`. A fork sets its own. |

To switch it off, set the schedule to `off`: YAAP then makes no call at start and none on the schedule. **Check now**
always works, as the one call you ask for yourself.

## The vault: keys and secrets

**Settings › Connections › Vault** holds YAAP's own keys (API keys, GitHub tokens) and the secrets the agent may use, at Global, per environment, and per project in the project settings. Values are encrypted with the master key, write-only, and every use is logged without the value.

- **Several keys per kind.** Give each key a name, such as `personal` or `client-org`. One is the environment's default (*Make default*); a key added in a project's settings replaces it for that project. The cards in Settings › Connections and Git and GitHub set the default key.
- **Secrets for the agent** are named values such as `DEPLOY_TOKEN`. The agent sees only the names and writes `{{secret:DEPLOY_TOKEN}}` in a command; YAAP passes the value as an environment variable when the command runs and masks it in all output. Such a command always asks. A project's secret overrides an environment's of the same name, and an environment's overrides a global one.
- **Expiry.** Set a date to see a warning two weeks ahead and a notification when it passes.
- **Use log.** Each entry's detail shows where it is used and what used it in the last 90 days.
- **API tokens** (kind *API token*, at Global) are listed here too; removing one revokes it. They are made in Settings › Account › Access (below).

## Access: password and API tokens

Every API route needs a signed-in browser or an API token. Open without either are only `GET /api/v1/health`, the sign-in state, signing in and the first-run password screen. YAAP still listens on `127.0.0.1` only.

- **The password** is one local account, stored as an Argon2id hash in the database. Set it on the first-run screen or with the setup script; change it in **Settings › Account › Access** (every other browser is then signed out); reset a forgotten one with `docker compose exec -it yaap python -m app.scripts.set_password --reset`.
- **The browser session** is an HttpOnly, SameSite=Strict cookie (`yaap_session`, with the port in its name when YAAP is not on port 80, such as `yaap_session_8080`). Requests that change something also send the session's CSRF token from the `yaap_csrf` cookie (also with the port in its name, such as `yaap_csrf_8080`) in the `X-CSRF-Token` header, and must come from YAAP's own page. A session ends after 30 days without use, on *Sign out*, or when the password changes. After five wrong passwords within a minute, signing in waits a minute.
- **API tokens** are made in **Settings › Account › Access**, one per program, optionally with an expiry date after which they are refused, and shown once. YAAP keeps each in the vault, encrypted, with only a hash to check it. Send it as `Authorization: Bearer <token>`. Purposes:

  | Purpose | Opens | Confined to |
  |---|---|---|
  | REST API | Every `/api/v1` route, as a signed-in browser, without CSRF. It cannot make or list tokens or change the password, but it can remove token entries in the Vault, which revokes them. | nothing; treat it like the password |
  | MCP server | YAAP's tools as an MCP server for an outside client | an environment, and optionally a project |
  | A2A agent | YAAP's A2A agent card and tasks | an environment, and optionally a project |

  *Revoke* (or removing the entry in the Vault) stops a token at once.
- **Claude Code and Codex** reach YAAP's tools at `/mcp` with a token YAAP makes for each reply; they need no API token.

## MCP servers

The `mcp_servers` setting (environment and project) adds outside tools through the Model Context Protocol. Settings › Connections › MCP servers and the project settings show each server with a test. A server is a named entry:

```json
{
  "github": {
    "transport": "http",
    "url": "https://api.githubcopilot.com/mcp/",
    "headers": { "Authorization": "Bearer {{secret:GITHUB_MCP_TOKEN}}" },
    "auto_tools": ["get_issue", "list_issues"]
  },
  "files": {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
  }
}
```

| Key | Meaning |
|---|---|
| `transport` | `stdio` (default), `http` (Streamable HTTP) or `sse` |
| `command`, `args`, `env` | For `stdio`: the program started in the environment's runner, in its `/workspace` (the environment's project folders); it must exist there (see [Extra packages](#extra-packages-for-the-runner)) |
| `url`, `headers` | For `http` and `sse` |
| `auto_tools` | Tools that run without asking; every other tool asks |
| `disabled_tools` | Tools that are never offered |
| `start_timeout`, `call_timeout` | Seconds (defaults 10 and 120) |
| `enabled` | Switch a server off |
| `description` | What the server is for, in a sentence; the agent sees it in the list of servers in its prompt |
| `list_all_tools` | `true`: offer every tool from the start; `false`: only through `find_tools`. Without it, a server with at most five tools is fully listed and a larger one is behind `find_tools` |

Secret placeholders work in `env` and `headers`; for a `stdio` server their values are passed to the runner each time it starts, never on a command line. A project can switch off an inherited server by setting it to `null`. Tools are named `server__tool`, and what they return counts as outside content. Servers stop after ten idle minutes and start again when needed.

**Tools behind a search.** The agent's prompt lists each server with its description and number of tools. A server with more than five tools does not send every definition with every model call: its tool names are listed, and the agent calls `find_tools` with what it needs (for example *create issue*), which adds the best matches for the rest of the reply. Set `list_all_tools` to `true` for a server whose tools the agent uses in almost every reply. Claude Code and Codex always see every tool (they read the list once per session).

## A2A agents

The `a2a_agents` setting (environment) names other AI agents that YAAP's agent may ask over the A2A protocol, with the tool `call_agent`. Settings › Connections › A2A agents shows them with a test that reads the agent card. An agent is a named entry:

```json
{
  "research": {
    "url": "https://agents.example.com/research",
    "token": "{{secret:RESEARCH_AGENT_TOKEN}}",
    "description": "Finds and summarises papers."
  }
}
```

| Key | Meaning |
|---|---|
| `url` | The agent card (an address ending in `.json`), or the address under which `/.well-known/agent-card.json` is found |
| `token` | Optional: one secret placeholder from the vault, sent as `Authorization: Bearer`; a plain token is refused |
| `description` | What the agent is good at; the agent sees it in the tool's description |
| `timeout` | Seconds to wait for the answer, 10 to 3600 (default 300) |
| `enabled` | `false` switches the agent off |

Every call asks first, and the answer counts as outside content. YAAP speaks A2A 0.3 over JSON-RPC; an agent that offers only gRPC or HTTP+JSON cannot be used. The token is sent only to the scheme, host and port of `url`: a card that sends messages elsewhere is refused.

## YAAP for other agents and MCP clients

Behind an API token, YAAP serves other machines. A token has a purpose (`a2a` or `mcp`) and an environment, and optionally a project; whatever its caller does stays inside that scope and under the permission settings there, never with full access. A call with no token, an unknown token or a token of the other purpose is refused with 401. YAAP listens on localhost unless you expose it; a client on another computer needs a way in, such as a tunnel, which is not part of YAAP.

| Endpoint | Purpose | What it is |
|---|---|---|
| `GET /.well-known/agent-card.json` | `a2a` | YAAP's agent card; it names `/a2a` as the endpoint |
| `POST /a2a` | `a2a` | A2A 0.3 over JSON-RPC: `message/send`, `tasks/get`, `tasks/cancel` (no streaming, no push notifications) |
| `POST /mcp/outside` | `mcp` | YAAP's tools over MCP's Streamable HTTP transport |

An A2A task waits up to 60 seconds for the reply (unless `configuration.blocking` is `false`) and otherwise returns as `working`; follow it with `tasks/get`. While the agent waits for your approval, the task's status message says so. A message with the `contextId` of an earlier task continues its conversation.

For an MCP client, add YAAP as an HTTP server with the token as a header. For Claude Code:

```
claude mcp add --transport http yaap http://localhost:8080/mcp/outside --header "Authorization: Bearer <token>"
```

The client gets YAAP's tools of the token's project (or, without a project, those that need none), without delegation and goals. Tools that ask keep the call open until you decide in YAAP, for at most 30 minutes, so give the client a long tool timeout. Each token's calls are listed in its conversation, *MCP client · token N*.

## Other settings

| Where | Setting |
|---|---|
| Settings › Agent › Language | The reply language (empty: the language of your message) and the work language (default English: code comments, documentation, commit messages, pull requests, project memory). Also per project in its settings. The interface stays English. The reply language also chooses the speech model: English uses Whisper's English-only `small.en`, any other language or none the multilingual `small`, told the language when YAAP knows its name (Dutch, Nederlands, `nl`, …) and detecting it otherwise. |
| Settings › Agent › Default skills | Skills pinned in every new conversation, for example an answer style |
| Settings › Git and GitHub | Author name and email for commits; every repository of the environment with its state; the GitHub repositories the token reaches, templates, and *New repository*. The token needs `repo` (classic) or Administration, Contents and Pull requests write (fine-grained). |
| Settings › Usage and audit › Usage and budget | Monthly budgets in US dollars for all environments and per environment. Only API-key use counts; subscriptions are never stopped. |
| Settings › Usage and audit › Audit log (environment) | Nothing to set: every model request that left YAAP, kept for a year and then removed automatically. Filters and the CSV and JSON export are on the page; the export holds no content and no secret values. See [usage.md](usage.md#the-audit-log). |
| Settings › Usage and audit › Telemetry | Each reply as an OpenTelemetry trace to an OTLP/HTTP endpoint, with optional headers and, when switched on, the prompt content; at Global, overridable per environment. See [usage.md](usage.md#opentelemetry-export). |
| Settings › Account › Profile (global) | Display name; time zone (taken from the browser until set) |
| Settings › Models › Utility and workers (global) | Embedding model for semantic search: `bge-m3` (local, Dutch and English) or off |
| Settings › System › Models (global) | The models YAAP downloads into the data folder (the search model and the speech models): state, download and remove. *Download the search model by itself after the first start* (`search_model_download`, on by default): the 2.3 GB search model downloads in the background when the first start is finished, and at start when the first start is over and the model is missing. Off: download it by hand. See [install.md](install.md#the-search-model). |
| Settings › Environment (environment) | The environment's name and colour |
| Settings › Environments (global) | Add and rename environments |
| Settings › System › Backup (global) | The schedule, destinations, recent runs; make, download, upload and restore backups (see [Backups](#backups)) |
| Settings › System › Updates (global) | How often YAAP checks for a newer version, or off, and which repository it reads (see [Update check](#update-check)) |
| Settings › System › Plugins (global) | Switch each plugin in `data/plugins/<name>/` on or off; takes effect after a restart. A plugin runs with YAAP's rights. See [plugins.md](plugins.md). |
| Settings › Targets (environment) | *Runner*: whether the environment's runner, where its commands, MCP servers over stdio and browser run, can reach the internet (*Runner network*, online by default). Computers over SSH the agent can work on: address, folders, *Allow full access* and *Whole computer* (both off by default). Their keys are vault entries of kind *SSH key of a target*. *Import a session* in the chat may also list and read Claude Code and Codex session files in the home folder of the target's user (`~/.claude/projects`, `~/.codex/sessions`), read-only and nothing else there. See [install.md](install.md#let-yaap-work-on-this-computer). |
| Project settings | Default preset and agent, where the agent works (the environment's runner, the project's own sandbox, or a target and the project's folder on it), language, permissions, default skills, knowledge folder (a project folder whose documents become knowledge), MCP servers, the project's own keys and secrets |
| Preferences menu (sidebar) | Dark or light theme, showing utility calls in the chat and verbose tool calls (stored in YAAP, for every browser), and reading every reply aloud (kept per browser) |

## What is personal and where it lives

Nothing personal belongs in YAAP's repository. Everything that is yours stays on your computer:

| What | Where | In git? |
|---|---|---|
| Master key | `.env` | Never; `.env` is ignored |
| Keys, tokens, secrets | The database in the data folder, encrypted with the master key | Never |
| Settings, conversations, usage | The database (`data/yaap.db`) | Never |
| The Blueprint (instructions, standards, skills, commands, agents) | `data/blueprint/global`, `data/blueprint/<environment>`, and `.blueprint/` inside each project | Not in YAAP's repository. Each layer is its own git repository, so you can push it to a private repository of your own (below). |
| Projects | `data/workspace/<environment>/<project>` | Each project's own repository |
| Attachments, knowledge, checkpoints, browser screenshots | The data folder | Never |
| Shell sessions of the agent (folder and exported variables) | `data/shells`; on a target, `yaap-shell-*` files in its temp folder | Never; not in backups. Sessions are gone two hours after their last use, the files on a target after a day |
| Claude Code and Codex sign-ins | `data/claude-code`, `data/codex` | Never |
| Your plugins | `data/plugins/<name>`; whether each is on is in the database | Not in YAAP's repository; in backups |
| Extra Docker settings for your computer | `docker-compose.override.yml` | Never; the file is ignored |

A new installation starts with an **empty Blueprint**. YAAP ships no standards or skills of its own, only three agent templates (developer, reviewer, architect) that you can add from the Blueprint.

### Keeping your Blueprint in your own repository

Each Blueprint layer is a git repository. To keep it safe and in sync between computers:

1. Create an empty **private** repository on GitHub, for example `my-blueprint-global`.
2. In the Blueprint, choose the level and use **Set remote** in the rail with the repository's HTTPS address.
3. **Push** sends the layer; YAAP fetches every 15 minutes and shows when the remote is ahead, so **Pull** brings in changes made elsewhere.

The project layer (`.blueprint/` inside a project) travels with the project's own repository.
