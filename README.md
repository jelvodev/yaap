<p align="center">
  <img src="docs/brand/yaap-logo-animated.svg" alt="YAAP" width="380">
</p>

<h3 align="center">Your AI Agent Platform</h3>

<p align="center">
  A personal, self-hosted AI development environment that runs in Docker on your own computer.<br>
  Any model, one agent you stay in control of, and your own way of working in every conversation.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-MIT-6272a4" alt="MIT licence"></a>
  <img src="https://img.shields.io/badge/runs%20in-Docker-7aa2f7" alt="Runs in Docker">
  <img src="https://img.shields.io/badge/backend-Python%20%C2%B7%20FastAPI-6272a4" alt="Python and FastAPI">
  <img src="https://img.shields.io/badge/frontend-TypeScript%20%C2%B7%20React-7aa2f7" alt="TypeScript and React">
  <img src="https://img.shields.io/badge/listens%20on-127.0.0.1%20only-282a36" alt="Listens on 127.0.0.1 only">
</p>

<p align="center">
  <a href="docs/install.md">Install</a> ·
  <a href="docs/usage.md">Use</a> ·
  <a href="docs/configuration.md">Configure</a> ·
  <a href="docs/architecture.md">Architecture</a> ·
  <a href="SECURITY.md">Security</a>
</p>

---

## Why YAAP

Most AI coding tools tie you to one vendor, one editor or one way of working. YAAP is one place for all of it, on your own machine:

- **Model-agnostic.** Claude, OpenAI and local models through Ollama, with the same tools, permissions and history. Use your Claude or ChatGPT subscription through Claude Code and Codex, or pay per use with an API key.
- **You stay in control.** The agent works in your projects, but every risky action waits for your approval, every change has a checkpoint you can roll back, and secrets are used by name without the agent ever seeing them.
- **Your way of working, written down once.** Your instructions, standards, skills and agents live in the **Blueprint**: Markdown files in git, at a global, an environment and a project level, followed by every conversation.
- **Separate worlds.** Environments such as *Work* and *Private* each have their own keys, projects, standards and budget, and never see each other's.

## What it does

| | |
|---|---|
| 💬 **Chat** | Claude, OpenAI and Ollama side by side; branching, attachments (images, PDF, Office, zip, whole folders), speech input and output, export, and search by words and by meaning in Dutch and English. |
| 🛠️ **An agent in your projects** | Reads, edits and runs code with your approval; shells that last the whole conversation, a headless browser, web search, project memory and knowledge. Per conversation you choose: *as set*, *full* or *read only*. |
| 🛟 **A safety net** | A checkpoint before every change, a guard against instructions hidden in web pages and files, and everything the agent runs in a separate runner without access to YAAP's keys. A sandbox container per project when you want one. |
| 📘 **The Blueprint** | Instructions, standards, skills, commands and agents as Markdown in git; import existing documents, install skills and packages from GitHub, `/check` a project against its standards, and see exactly what goes into the prompt. |
| ⏱️ **Longer work** | Goal mode, delegation to a cheaper worker model or to Claude Code and Codex, scheduled tasks, and context control with automatic compaction and `/compact`. |
| 🌿 **Git and GitHub** | Commit with a proposed message, branches, pull requests with their checks, projects from template repositories, for projects and Blueprint layers alike. |
| 📝 **Files and documents** | A file browser and editor beside the chat, and documents you and the agent edit together. |
| 🖥️ **This computer** | Optionally, the agent works in folders of your own computer over SSH, with its own user and asking before every change. |
| 🔐 **The vault** | API keys, tokens and secrets encrypted with your master key, several per kind, with expiry dates and a use log. |
| 💾 **Backups and usage** | Daily backups to a folder or a network share, restore from the interface, and cost per environment, project, model and month with budgets. |
| 🔌 **Open to other tools** | MCP servers for the agent, other agents over A2A, YAAP's own tools as an MCP server for outside clients, and plugins of your own. |

## How it fits together

```mermaid
flowchart LR
    you(["You, in the browser"]) -->|"127.0.0.1:8080"| yaap

    subgraph docker["Docker on your computer"]
        yaap["YAAP<br/>FastAPI + React"]
        helper["yaap-sandbox<br/>starts the runners"]
        proxy["docker-proxy<br/>filtered Docker API"]
        runner["Runner<br/>what the agent runs"]
        searxng["SearXNG<br/>web search"]
        data[("./data<br/>database · Blueprint · projects · backups")]
    end

    yaap --> helper
    helper --> proxy
    helper --> runner
    yaap --> searxng
    yaap --> data
    yaap --> models["Claude · OpenAI · Ollama"]
    yaap --> github["GitHub"]
```

More in [docs/architecture.md](docs/architecture.md).

## Quick start

You need [Docker Desktop](https://docs.docker.com/get-docker/) (Windows, macOS) or Docker Engine with Compose v2 (Linux), and git.

```bash
git clone https://github.com/jelvodev/yaap.git
cd yaap
```

Run the setup script. It creates `.env` with a new master key, builds and starts YAAP, waits until it answers and asks for YAAP's password.

**Windows** (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

**macOS or Linux**:

```bash
./scripts/setup.sh
```

Then open <http://127.0.0.1:8080> and sign in. The first start guides you through a model connection, a first project, backups and YAAP's starter Blueprint. To use a subscription, sign in to its CLI first; for example, Claude Code with your Claude subscription:

```bash
docker compose exec -it yaap claude auth login
```

> [!IMPORTANT]
> **Keep a copy of `YAAP_MASTER_KEY` from `.env`** in a password manager. It encrypts your keys and secrets; without it, a backup cannot be read.

The first build downloads a few GB (toolchains, a headless browser, a local embedding model) and takes several minutes. Other providers, updating and troubleshooting are in [docs/install.md](docs/install.md).

## Your data stays yours

Everything personal lives in two places that git ignores: `.env` with the master key, and the data folder `./data` with the database, the Blueprint, your projects, attachments, sign-ins and backups. Nothing personal is part of this repository, and a new installation starts with an empty Blueprint until you install [YAAP's starter](blueprint/starter). Nothing leaves your computer except the model calls, web searches, pages and GitHub requests you cause. See [what is personal and where it lives](docs/configuration.md#what-is-personal-and-where-it-lives).

## Security

YAAP is built for one person on one computer. It has one password, listens on `127.0.0.1` only and answers only to its own addresses; **do not expose it to a network**. The agent runs commands in a separate runner, with your approval unless you allow more. Read [SECURITY.md](SECURITY.md) before you change the defaults, and report vulnerabilities privately as described there.

## Documentation

| | |
|---|---|
| [Installing](docs/install.md) | Setup, signing in, connecting models, the runner, updating, moving, troubleshooting |
| [Using](docs/usage.md) | A tour of the chat, projects, the Blueprint and Settings |
| [Configuring](docs/configuration.md) | Environment variables, levels, connections, permissions, the vault, backups, MCP and A2A |
| [Architecture](docs/architecture.md) | How YAAP is built, with diagrams |
| [Plugins](docs/plugins.md) | Writing a plugin of your own |
| [Blueprint packages](docs/blueprint-packages.md) | Sharing standards, skills and agents as a package |
| [Roadmap](docs/roadmap.md) | What comes in the next versions, and the wish list |
| [Credits](docs/credits.md) | Where the ideas in YAAP's starter come from |
| [Contributing](CONTRIBUTING.md) | Development setup, tests and conventions |

## Status

YAAP is a personal project, built and used daily by one developer, and shared in case it is useful to you.

Issues and ideas are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

[MIT](LICENSE) © 2026 The YAAP authors
