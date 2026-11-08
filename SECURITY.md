# Security

YAAP is a personal tool that runs on your own computer. This page says what runs where, what the agent can reach, what is protected and what is not, and how to report a problem. How it is built is in [docs/architecture.md](docs/architecture.md).

## What runs where

| Where | What runs there | What it holds |
|---|---|---|
| **YAAP's process** (the `yaap` container) | The web server and API, the agent loop, the vault, file tools, checkpoints, YAAP's own git (status, diff, commit, fetch, push, branches), semantic search, the editor, Claude Code and Codex (which get only YAAP's tools), your plugins | The master key, the database `/data/yaap.db`, every environment's projects and Blueprint, the Claude Code and Codex sign-ins, backups |
| **The runner of an environment** (a container per environment, started by the `yaap-sandbox` helper) | Everything the agent or a third party chooses to run: `run_command`, background processes, shell sessions, MCP servers over stdio, language servers, the agent's browser | Only that environment's project folders, mounted as `/workspace`, and the secrets passed to one command or MCP server |
| **A project's sandbox** (optional, per project) | The commands of that project instead of the runner | Only that project's folder; no network unless you switch it on |
| **A target** (a computer over SSH, also the one that runs YAAP) | The commands and file changes of the projects and conversations that chose it | Whatever the SSH user on that computer may do |

YAAP's process never shares a network with a runner or sandbox; it reaches them only through the helper's relay, over SSH with keys and host keys it made and pinned. The helper reaches Docker only through a socket proxy that allows its few calls, and YAAP itself has no Docker socket.

## What the agent's commands can and cannot reach

A command the agent runs in the runner **can** reach:

- the project folders of its own environment (read and write), including their `.git` folders;
- the secrets it is given: a `{{secret:NAME}}` it names, passed as a variable for that one command (such a command always asks first), and the configured values of an MCP server it starts;
- the internet, unless *Runner network* in Settings › Targets is *Offline*;
- open ports of other runners and sandboxes on the same Docker network (a dev server), never their files.

It **cannot** reach the master key, the database, the vault's other values, the Claude Code and Codex sign-ins, backups, another environment's projects, or YAAP's API and SearXNG. The runner has no `/data` folder, and its processes are not YAAP's, so YAAP's memory and start environment are out of reach. Without the runner, a command is refused; YAAP never runs it in its own container.

**YAAP's own git** runs in YAAP's process on folders the runner can write. So that a command cannot plant code there that YAAP would run with its rights, every git YAAP starts ignores the system and user git configs, switches off hooks, the fsmonitor, pagers, editors, askpass and credential helpers, a configured ssh command, upload-pack and receive-pack commands, signing and submodule recursion with settings a repository cannot override, and allows only the file, http(s) and ssh transports. Before it runs git in a repository, YAAP reads that repository's config files (and those of its submodules) and refuses when they set anything beyond plain settings such as remotes, branches, user and core options: filter, diff and merge drivers, textconv, includes, upload-pack and receive-pack commands, gpg programs or `core.worktree`. The refusal names the key to remove. A `.git` file or `objects/info/alternates` that points outside the project folder is refused as well. Claude Code, which runs git in the project folder for its own context, gets the same settings and the same check, and loads no project settings (`.claude/settings.json` could name hooks).

Checkpoints are kept in YAAP's own shadow repositories under the data folder, which no command can reach, so they also hold after a command changed the project's `.git`.

## Sign-in and tokens

- **One user, with a password.** Every API route needs a signed-in browser (an HttpOnly, SameSite=Strict session cookie, with a CSRF token on every change) or an API token from Settings › Account › Access. The password is stored as an Argon2id hash; sign-in waits a minute after five wrong passwords; a session ends after 30 days without use. Until a password is set (right after installing or updating), whoever opens YAAP first sets it, so set it at once. A forgotten password is reset with `docker compose exec -it yaap python -m app.scripts.set_password --reset`.
- **API tokens** are made per program, shown once, kept encrypted in the vault and revocable. A REST API token acts as you; MCP and A2A tokens are confined to an environment (and optionally a project), never get full access, and their approvals wait for you as in the chat.
- **Machines inside YAAP** (Claude Code, Codex and CLI workers calling YAAP's tools) use a token YAAP makes per reply.

## Only local addresses, only local pages

YAAP listens on `127.0.0.1:8080` only. It answers only when it is asked for by `localhost`, `127.0.0.1` or `[::1]` (plus names you add in `YAAP_ALLOWED_HOSTS`); any other `Host` gets 400, so a web page cannot reach YAAP by pointing its own domain at your computer (DNS rebinding). A browser request from another site's page gets 403, for the API, the interface, `/mcp`, `/a2a`, `/mcp/outside` and plugin pages alike. The one exception is the static files a plugin page's sandboxed frame loads (`/plugin-sdk.js` and the page's own files), which pass with `Origin: null`. Programs that send no `Origin` (curl, scripts with a token) are not affected.

**Do not publish the port to a network, put it behind a tunnel or a reverse proxy, or run YAAP on a shared machine.** The password is a second lock, not a reason to expose YAAP: TLS and the hardening of a public endpoint are not part of it.

## The hardened process

- YAAP's server process is marked *not dumpable* on Linux, so nothing running as the same user can read its memory or the environment it started with in `/proc`.
- YAAP removes the master key from its own environment once it has read it, and warns at start when a key file (`YAAP_MASTER_KEY_FILE`) is readable by others.
- The container runs without any Linux capabilities and with `no-new-privileges`, so no setuid program can raise a process's rights.
- Nothing YAAP starts (git, checkpoints, OCR, Claude Code, Codex, and in development commands and MCP servers) inherits YAAP's own variables, the master key among them, or provider and GitHub tokens from its environment; Claude Code and Codex get only the few variables they need. YAAP passes a credential only where it is meant to go, such as the GitHub token for one fetch or push, through git's environment and only for github.com.

## You approve what changes things

Writing files, running commands and committing can each ask first; pushing and a command with a secret always ask. The access choice *full* turns asking off for one conversation; with the runner that leaves the environment's own project files and the secrets you gave the agent within its reach, nothing of YAAP's. After the agent read outside content (a web page, an attachment, a skill from GitHub, the output of an MCP server or another agent), every changing action asks for the rest of that reply, and optionally the utility model marks requests that seem to follow that content. Checkpoints let you roll back file changes per action.

## The preview of a project's app

The preview panel shows a dev server the agent started on an address of its own, `YAAP_PREVIEW_PORT` (8081 by default, on `127.0.0.1` like YAAP). Its pages are the project's code, so they are treated like another site:

- **Stripped.** Every request through the preview loses YAAP's cookies (every cookie whose name starts with `yaap_`) and its `Authorization` and proxy headers before it reaches the dev server; a cookie named like YAAP's that the dev server tries to set is dropped. The dev server sees its own `Host` and `Origin`.
- **Refused by YAAP.** YAAP's browser boundary refuses any request or WebSocket whose `Origin` is on the preview's port, also with a valid session and CSRF token. Browsers send YAAP's SameSite cookies to pages on another port of the same host, so this is what keeps a preview page from changing anything in YAAP.
- **The token.** A preview opens only with a link YAAP's API makes for one running process with a port, when you are signed in. The link's token is signed with a key that is new at every start and kept only in memory, works once and for two minutes, and is exchanged for an HttpOnly, SameSite=Strict cookie of the preview's own that is valid for that process and port only, while the process runs. A request without it, with a forged or expired one, from another host name (DNS rebinding) or from another page's origin is refused.
- **The way to the dev server.** In the runner or a sandbox the preview uses YAAP's own SSH connection; the sandbox image allows that connection to forward to `localhost` inside the container and nowhere else.

What remains a risk: a preview page can read YAAP's CSRF cookie (it is not HttpOnly, and cookies are shared between ports) and overwrite YAAP's cookies from script, which can sign you out; the CSRF token alone opens nothing, because the origin check refuses the preview. Like any page you open, a preview page can try to trick you within its own frame. The preview listener, like YAAP, is not meant to be exposed beyond your computer.

## Keys and secrets

- API keys, tokens and secrets are stored **encrypted** in the database with `YAAP_MASTER_KEY`, which lives outside the data folder in `.env` (or in a file, such as a Docker secret). A copy of the data folder or a backup alone does not expose them.
- They are **write-only**: the interface and the API never return a value, at most its last four characters (none for a short secret).
- The master key can be changed without losing anything: YAAP re-encrypts every value on start (see [docs/configuration.md](docs/configuration.md#the-master-key)).
- Every use of a key or secret is logged in the Vault (what used it and when, never the value), so an unexpected use shows.
- The agent never sees YAAP's own keys. Secrets meant for the agent are used by name (`{{secret:NAME}}`), and every tool output is masked.
- YAAP never handles subscription credentials. Claude Code and Codex keep their own sign-in in the data folder.

Keep `YAAP_MASTER_KEY` in a password manager. If it is lost, stored keys must be entered again and backups cannot be decrypted. If it leaks, together with a copy of your data folder or a backup, replace every stored key at its provider.

## Plugins run with YAAP's rights

A plugin in `data/plugins` is Python code that runs inside YAAP's process: it can read the data folder, use the vault as YAAP does, run programs and reach the network. YAAP therefore loads only your own plugins from that folder, never from a link or a hub; each is off until you switch it on in Settings › System › Plugins with that warning, and its manifest must name what it registers before its code runs. A plugin's page runs in a sandboxed frame without access to YAAP's API. Only switch on code you wrote or read.

## What YAAP does not protect against

- Malware or other users on the same computer, and anyone who can run `docker` there.
- What a command may do by design: change or delete the files of every project in its environment, use the secrets it was given, and reach the internet when the runner is online. Checkpoints and git help you undo file changes; they are not a backup.
- A model or tool you approve doing something you did not intend: read approval cards before you approve, and use *full* access only for work you would let run unattended.
- A computer you added as a target: there the agent can do what its SSH user can, and nothing can be rolled back.
- Your plugins, third-party MCP servers and skills: they run with the rights they are given. Skills from GitHub are reviewed before installing and pinned to a commit, but read them.
- A race against YAAP's own git. YAAP checks a repository's config and then runs git, which reads it again; a background process that keeps rewriting `.git/config` could slip a filter driver in between and have it run in YAAP's container during a status, diff, commit, branch switch or pull. Everything else listed above holds regardless. If you let the agent run background processes without asking, keep an eye on what they are.
- A project remote that is a local path. YAAP honours it, and its fetch (also the periodic one) reads that path with YAAP's rights, so a command that points a remote at another environment's repository could copy that history into its own project. Use https remotes, and check the remotes in Settings › Git and GitHub when something looks odd.
- Costs at a provider: set a spending limit in each provider's console as well. YAAP's budgets only count what it sees.
- Exposure beyond your own computer (see above).

## Reporting a vulnerability

Please report security problems privately through GitHub's **Report a vulnerability** on the repository's Security tab, not in a public issue. Describe what you found and how to reproduce it. You will get an answer as soon as possible; this is a personal project without a formal response time.
