# Architecture

How YAAP is put together, in six diagrams and a table of features. This page is the map of the code.

## 1. Overview

YAAP is one application container next to a SearXNG container, reached from your browser on `127.0.0.1:8080` (and on `127.0.0.1:8081` for the preview of a project's app), with a helper that starts a *runner* per environment: the container where the agent's commands, MCP servers over stdio and browser run (section 1a). Everything it keeps is in one data folder.

```mermaid
flowchart TB
    browser["Browser<br/>React frontend: Chat · Blueprint · Settings"]

    subgraph host["Your computer (Docker)"]
        subgraph yaap["yaap container"]
            api["FastAPI backend<br/>/api/v1 + server-sent events"]
            static["Built frontend<br/>(static files)"]
            preview["Preview listener :8081<br/>a project's dev server"]
            clis["Claude Code CLI · Codex CLI"]
            onnx["Local embedding model<br/>bge-m3 (ONNX)"]
            tess["Tesseract OCR"]
            whisper["Local speech model<br/>faster-whisper"]
        end
        searxng["searxng container<br/>web search, no key"]
        runner["runner per environment<br/>commands · stdio MCP servers · language servers<br/>Playwright MCP + Chromium"]
        data[("Data folder /data<br/>yaap.db · blueprint · workspace<br/>files · knowledge · backups · sign-ins")]
    end

    subgraph outside["Outside services"]
        anthropic["Anthropic API"]
        openai["OpenAI API"]
        ollama["Ollama"]
        github["GitHub"]
        mcp["MCP servers"]
        web["Web pages"]
        a2a["A2A agents"]
        clients["Outside clients<br/>MCP and A2A, with a token"]
    end

    browser -- "HTTP + SSE, allowed hosts only<br/>(localhost by default)" --> api
    clients -- "/mcp/outside · /a2a" --> api
    api --> static
    browser -- "preview pages" --> preview
    preview -- "SSH through the helper" --> runner
    api --> clis
    api -- "SSH through the helper" --> runner
    api --> onnx
    api --> tess
    api --> whisper
    api --> a2a
    api --> searxng
    api --> data
    clis --> anthropic
    clis --> openai
    api --> anthropic
    api --> openai
    api --> ollama
    api --> github
    api -- "MCP over HTTP" --> mcp
    runner --> web
    runner -- "packages, stdio MCP servers" --> mcp
    api --> web
    searxng --> web
```

- **Frontend.** React and Vite, built into the image and served by FastAPI. Commands are plain HTTP requests; replies, tool calls and approvals stream back as server-sent events.
- **Backend.** One FastAPI process. Replies run on the server, apart from the browser connection, so closing the tab does not stop them.
- **Models.** Claude and OpenAI through an API key, or through the vendors' own CLIs (Claude Code, Codex) signed in with your subscription; local models through Ollama.
- **Data.** SQLite for state, git repositories for the Blueprint and projects, plain folders for files. One folder to back up.

## 1a. Where commands run: the runner and its networks

What the agent runs (`run_command`, `start_process`, shell sessions, MCP servers over stdio, language servers, the browser) runs in the *runner* of its environment, not in YAAP's container. A runner is a sandbox container of the whole environment: it mounts only `data/workspace/<environment>` as `/workspace`, so it cannot read YAAP's database, the master key, the Claude Code and Codex sign-ins, or another environment's projects. A project that chose its own sandbox or a computer over SSH works there instead.

```mermaid
flowchart LR
    subgraph default["network default"]
        yaap["yaap<br/>FastAPI, file tools, git,<br/>checkpoints, Claude Code, Codex"]
        searxng["searxng"]
    end
    subgraph control["sandbox-control (internal)"]
        helper["yaap-sandbox helper<br/>API :8000 · SSH relay :2222"]
    end
    subgraph dockerapi["docker-api (internal)"]
        proxy["docker-socket-proxy<br/>containers + images only"]
    end
    subgraph online["yaap-sandbox-online"]
        r1["runner e1<br/>/workspace = data/workspace/work"]
        r2["runner e2<br/>/workspace = data/workspace/home"]
    end
    subgraph offline["yaap-sandbox-offline (internal, no route out)"]
        p7["sandbox p7 of a project<br/>/workspace = its project folder"]
    end
    socket[("Docker socket")]
    internet(("internet"))

    yaap --- control
    helper --- proxy
    proxy --- socket
    helper -- "relays SSH" --> r1
    helper -- "relays SSH" --> r2
    helper -- "relays SSH" --> p7
    r1 --> internet
    r2 --> internet
```

- **YAAP never shares a network with a runner or sandbox.** It reaches one only through the helper's relay: it sends the helper's token and the runner's key (`e<environment>`), the helper checks the container's labels and copies bytes; SSH runs end to end with a key YAAP made per runner and a host key YAAP made and pinned. From a runner, YAAP's API and SearXNG are unreachable.
- **Online by default.** Runners join `yaap-sandbox-online` (installing packages is normal work); the environment setting *Runner network* (Settings › Targets) moves its runner to `yaap-sandbox-offline`. Project sandboxes stay offline unless the project switches the network on. Containers on the same network can reach each other's open ports (a dev server), never each other's files.
- **The project folder is shared, not copied.** YAAP's file tools, checkpoints, git tools, semantic search and the editor work on `data/workspace/...` in YAAP's container; the runner sees the same folder at `/workspace/<project>`. Claude Code and Codex stay in YAAP and use only YAAP's tools over MCP, so their commands also go to the runner.
- **YAAP's own git runs nothing from the repository.** Because a command in the runner can write a project's `.git`, every git YAAP starts (`app/git/safety.py`: the project panel, the git tools, the periodic fetch, the Blueprint and checkpoints) skips the system and user configs, takes fixed settings at command scope that switch off hooks, the fsmonitor, pagers, editors and credential helpers, and first checks the repository's own config against the keys YAAP honours; anything else (a filter, a diff or merge driver, an include) makes it refuse git there with the key to remove. Claude Code, which runs git in the project folder for its context, gets the same.
- **Lifecycle.** The helper creates a runner on first use, from `yaap-sandbox:local` (or the image with extra packages named in the override file), starts it when it stopped, and YAAP stops it after an hour without use (no running background process). A rebuilt image makes the runner again on its next start.
- **Without the helper** (not running, image not built) commands are refused with what to do; YAAP never runs them in its own container. `YAAP_RUNNER=off` does run them in YAAP's process, for development without Docker.

## 2. Backend modules

```mermaid
flowchart LR
    subgraph api["api/ (routes, SSE)"]
        routes["conversations · projects · blueprint · git<br/>settings · credentials · secrets · backups · tasks …"]
    end

    subgraph core["core/"]
        chat["chat, replies<br/>(runs in the background)"]
        context["prompts, turn_context<br/>(context assembly)"]
        hygiene["summaries, trimming"]
        search["search, search_index, embeddings"]
        usage["usage_report, budget"]
        backup["backup, restore"]
    end

    subgraph agent["agent/"]
        runner["runs, delegation"]
        tools["tools, toolbox, toolkit"]
        gate["gate (every tool call) · permissions (the decision)<br/>policy (per-tool choices) · guard (outside content)"]
        mcpserver["mcp_server<br/>(YAAP's tools over MCP)"]
    end

    subgraph services["services"]
        blueprint["blueprint/"]
        projects["projects/ (memory)"]
        knowledge["knowledge/"]
        git["git/"]
        checkpoints["checkpoints/"]
        scheduling["scheduling/"]
        browser["browser/"]
        websearch["websearch/"]
        mcpclient["mcp_client/"]
        secrets["credentials/, secrets/"]
    end

    providers["providers/<br/>anthropic · openai · claude_code<br/>codex · ollama · fake"]
    targets["targets/ · sandbox/ · runner/<br/>container · SSH · sandbox · runner"]
    storage[("storage/<br/>SQLAlchemy models, Alembic")]

    routes --> chat
    routes --> services
    chat --> context
    chat --> providers
    chat --> runner
    context --> blueprint
    context --> projects
    runner --> tools
    tools --> gate
    mcpserver --> tools
    tools --> targets
    tools --> checkpoints
    tools --> knowledge
    tools --> browser
    tools --> websearch
    tools --> mcpclient
    tools --> secrets
    providers --> mcpserver
    core --> storage
    services --> storage
```

| Folder in `backend/app/` | Responsibility |
|---|---|
| `api/` | HTTP routes and event streams under `/api/v1`, with an OpenAPI description from which the frontend's types are generated |
| `core/` | The chat and its background replies, context assembly, summaries and trimming, search and embeddings, usage and budgets, settings resolution, the choice of provider, model and key (`model_choice`), the model policy, the audit log, export, backup and restore |
| `agent/` | The agent's tools, the permission gate (`gate`) with its pure decision (`permissions`) and approvals, the outside-content guard, delegation, processes, and YAAP's own MCP server through which Claude Code and Codex call YAAP's tools |
| `agents/` | Agent definitions and run records |
| `auth/` | The local account, browser sessions, API tokens, and the host and origin checks on every request |
| `vault/` | Encrypted entries per kind with defaults, expiry and the use log, and master key rotation |
| `environments/`, `presets/` | Environments, and model presets with their roles |
| `providers/` | One adapter per model vendor behind the neutral `ModelProvider` interface; the only place for vendor code. `cli_sign_in.py` signs in to Claude Code and Codex from the browser (`/api/v1/claude-code/sign-in`, `/api/v1/codex/sign-in`): one sign-in process per CLI, each provider's spec says the command, the expected link hosts and whether a code is pasted back |
| `targets/`, `sandbox/`, `runner/` | Where the agent works: a file system confined to a folder, and a command executor: a computer over SSH, a project's sandbox, or the environment's runner (YAAP's own process only with `YAAP_RUNNER=off`) |
| `blueprint/` | Layers, merging, import, rules and skills from GitHub |
| `projects/`, `knowledge/` | Projects, project memory and memory suggestions, project knowledge |
| `git/`, `checkpoints/` | Git for projects and Blueprint layers; snapshots before each changing action |
| `credentials/`, `secrets/` | Encrypted keys and agent secrets |
| `scheduling/` | Scheduled tasks, and the backup schedule |
| `backups/` | Backup runs and their destinations: a folder or an SMB share |
| `files/`, `documents/` | The Files tab and its editor; the shared document beside a conversation |
| `imports/` | Claude Code and Codex sessions imported as conversations |
| `github/` | The GitHub API: repositories, pull requests with their checks, templates |
| `speech/` | Transcription with a local Whisper model, and the text read aloud |
| `a2a/` | Other agents over A2A as a tool, and YAAP's own agent card and tasks |
| `plugins/` | The user's own plugins: manifest, loading and the stable interface |
| `browser/`, `websearch/`, `mcp_client/` | The agent's headless browser (in the runner), web search through SearXNG, outside MCP servers (stdio ones in the runner) |
| `attachments/` | Uploads, extraction and OCR |
| `lsp/` | Language servers, one per project and language, run where the project's commands run |
| `outline/`, `repomap/` | A file's outline; the repository map at the start of a conversation |
| `preview/` | The preview of a project's app: a second listener that forwards to a dev server the agent started |
| `tracing/` | What a run did step by step, context snapshots, and the OpenTelemetry export |
| `evals/`, `replays/` | Eval cases, their checks and runs; replays of a run from one of its model calls |
| `learning/` | Signals from run steps, and the proposals made from them |
| `reviews/` | Reviews of changed code by a model of another vendor |
| `scripts/` | Command-line scripts: OpenAPI export, password reset, measurements |
| `storage/` | Database models and migrations |

The helper is a separate package, `backend/yaap_sandbox/`: its API on port 8000 starts, stops and removes runners and sandboxes through the socket proxy, and its relay on port 2222 forwards YAAP's SSH connection to them.

## 3. One reply, step by step

What happens when you send a message in a project conversation through an API-key connection. Through Claude Code or Codex the loop runs inside that CLI, which calls the same tools through YAAP's MCP server, with the same gate and approvals.

```mermaid
sequenceDiagram
    actor You
    participant UI as Frontend
    participant Chat as core/chat
    participant Ctx as Context builder
    participant Model as Provider
    participant Gate as Permission gate
    participant Tool as Tool + target
    participant DB as Storage

    You->>UI: message
    UI->>Chat: POST message
    Chat->>DB: store message
    Chat-->>UI: events (SSE)
    Chat->>Ctx: assemble request
    Ctx-->>Chat: base prompt, Blueprint, memory, history
    loop until the model ends its turn
        Chat->>Model: request
        Model-->>Chat: text, reasoning, tool call
        Chat-->>UI: text and reasoning as they arrive
        Chat->>Gate: tool call
        alt needs approval
            Gate-->>UI: approval card
            You->>UI: approve or deny
            UI->>Gate: decision
        end
        Gate->>Tool: run (checkpoint first if it changes files)
        Tool-->>Chat: result (masked, trimmed if long)
        Chat-->>UI: tool result
    end
    Chat->>DB: store reply, runs, usage
    Chat-->>UI: done
```

The request is built in a fixed order, stable parts first, so providers can cache the prefix: the base prompt, `instructions.md`, the summary and rules of every standard, an index of skills, commands and agents, the agent's role with the project's instructions and memory, the history, and finally your message with the date and time.

## 4. Levels and inheritance

```mermaid
flowchart TB
    global["Global<br/>settings · Blueprint layer data/blueprint/global"]
    env["Environment (e.g. Work)<br/>settings · keys · secrets · projects<br/>Blueprint layer data/blueprint/&lt;environment&gt;"]
    project["Project<br/>settings · secrets<br/>Blueprint layer &lt;project&gt;/.blueprint/ · memory.md"]
    conversation["Conversation<br/>preset · agent · access · pinned skills"]

    global --> env --> project --> conversation
```

- A setting resolves to the nearest level that sets it: project, environment, global, then the default.
- Blueprint layers merge in the same order: a file at the same path replaces the inherited one, `disabled: true` switches it off, and a skill with `extends: true` adds to it.
- Keys, the GitHub token and projects exist only per environment, so one environment never sees another's credentials, standards or memory.

## 5. Data

```mermaid
flowchart LR
    subgraph data["Data folder (/data)"]
        db[("yaap.db<br/>settings · conversations · runs<br/>usage · encrypted keys and secrets<br/>search index")]
        bp["blueprint/<br/>git repositories per layer"]
        ws["workspace/<br/>project folders (git)"]
        files["files/ · knowledge/<br/>attachments, documents"]
        other["checkpoints/ · outputs/ · shells/<br/>traces/ (context snapshots)<br/>evals/ · browser/"]
        extra["plugins/ · models/whisper/"]
        signins["claude-code/ · codex/<br/>CLI sign-ins"]
        backups["backups/<br/>zips"]
    end
    envfile[".env<br/>YAAP_MASTER_KEY"]

    db -- "in a backup" --> backups
    bp -- "in a backup" --> backups
    ws -- "in a backup" --> backups
    files -- "in a backup" --> backups
    extra -- "plugins in a backup" --> backups
    envfile -. "encrypts keys and secrets" .-> db
```

| | In a backup | In YAAP's git repository |
|---|---|---|
| Database, Blueprint, projects, attachments, knowledge, plugins | yes | never |
| Checkpoints, long tool outputs, screenshots | no (rebuilt as you work) | never |
| Eval runs, the agent's browser data | no | never |
| Claude Code and Codex sign-ins | no (sign in again after a restore) | never |
| The speech model (`models/whisper`) | no (downloaded again on first use) | never |
| Master key (`.env`) | no (keep it apart) | never |

A backup leaves out dependency and build folders in projects (`node_modules`, `.venv`, `dist`, `build` and the like).

Nothing leaves your computer except the requests you cause: model calls to the provider you chose, web search and pages the agent reads, git operations with GitHub, calls to MCP servers and A2A agents you configured, traces to an OpenTelemetry endpoint when you set one, and the one-time download of the speech model from Hugging Face when you first use the microphone. Embeddings for search and transcription are made locally.

## 6. Background work

```mermaid
flowchart LR
    start(["YAAP starts"]) --> migrate["Harden the process<br/>finish a pending restore<br/>prune old tool outputs and snapshots<br/>migrate the database<br/>re-encrypt after a key change · load plugins"]
    migrate --> loops

    subgraph loops["Running in the background"]
        replies["Replies and goal turns<br/>(independent of the browser)"]
        tasks["Scheduler, every minute<br/>scheduled tasks and backups<br/>(a missed backup once at start)"]
        index["Search index<br/>on start, every minute, after each reply"]
        fetch["Git fetch, every 15 minutes<br/>repositories with a remote"]
        idle["Idle stops<br/>MCP servers 10 min · browsers and speech model 15 min<br/>processes 1 h · shell sessions 2 h<br/>runners and sandboxes 1 h, checked every 5 min"]
        prune["Pruning<br/>checkpoints over 30 days, when a new one is taken<br/>context snapshots over 30 days or 2 GB, daily"]
    end
```

## 7. Features per screen

| Feature | Where | Provided by |
|---|---|---|
| Conversations, branching, export | Chat | `core/chat`, `core/conversations`, `core/export` |
| Attachments, OCR | Chat input | `attachments/` |
| Agent tools and approvals | Chat (tool cards) | `agent/` |
| Access per conversation | Chat input | `agent/policy` |
| Goal mode, steering, queue | Chat (strips above the input) | `core/goals`, `api/activity` |
| Context meter, summaries | Chat header | `core/turn_context`, `core/summaries` |
| Project git | Project panel › Changes, History | `git/` |
| Project memory and suggestions | Project panel › Memory | `projects/memory`, `projects/suggestions` |
| Knowledge | Project panel › Knowledge | `knowledge/` |
| Scheduled tasks | Project panel › Tasks | `scheduling/` |
| Checkpoints | Chat (per tool call), Project panel › Checkpoints | `checkpoints/` |
| Processes, project settings, secrets, MCP servers | Project panel › Project, Settings › Connections › MCP servers | `agent/processes`, `secrets/`, `mcp_client/` |
| Blueprint items, import, skills from GitHub | Blueprint | `blueprint/` |
| Agents and their runs | Blueprint › Agents | `agents/`, `agent/runs` |
| Search | Ctrl K, Search page | `core/search`, `core/search_index` |
| Connections, Ollama | Settings › Connections | `providers/` |
| Keys and secrets, use log | Settings › Connections › Vault | `vault/`, `secrets/` |
| Presets and roles | Settings › Models › Presets | `presets/` |
| Permissions | Settings › Agent › Permissions | `core/settings`, `agent/policy`, `agent/permissions` |
| Default skills, languages | Settings › Agent › Default skills, Language | `core/settings` |
| Model policy | Settings › Models › Policy | `core/model_policy` |
| Repositories | Settings › Git and GitHub | `git/` |
| Usage and budgets | Settings › Usage and audit › Usage and budget | `core/usage_report`, `core/budget` |
| Audit log | Settings › Usage and audit › Audit log | `core/audit` |
| Telemetry (OpenTelemetry export) | Settings › Usage and audit › Telemetry | `tracing/` |
| Backup and restore, schedule, destinations | Settings › System › Backup | `core/backup`, `core/restore`, `backups/` |
| Context limit and compaction, repository map | Settings › Models › Context, Chat header | `core/context_control`, `repomap/` |
| Preview of a project's app | Beside the chat | `preview/` |
| Files and editor | Project panel › Files | `files/` |
| Shared document | Beside the chat | `documents/` |
| Session import | Chat | `imports/` |
| Speech | Chat input, Preferences menu | `speech/` |
| Workers for delegated tasks | Settings › Models › Utility and workers | `agent/delegation` |
| Runner, sandboxes, computers over SSH | Settings › Targets, Project panel › Project | `runner/`, `sandbox/`, `targets/` |
| Pull requests, templates | Project panel › Branches, Settings › Git and GitHub | `github/` |
| Blueprint packages | Blueprint | `blueprint/packages`, `blueprint/package_updates` |
| First start, YAAP's starter | `/welcome`, Blueprint › Add from a package | `core/first_run`, `blueprint/starter` |
| Sign-in and API tokens | Settings › Account › Access | `auth/` |
| A2A agents, YAAP for outside clients | Settings › Connections › A2A agents, Settings › Account › Access | `a2a/`, `agent/mcp_server` |
| Plugins | Settings › System › Plugins | `plugins/` |
