# Installing YAAP

YAAP runs as a few containers on your own computer: YAAP itself, SearXNG (the web search it uses), and a small helper with a filtering proxy that start the agent's runners and sandboxes (see [The runner](#the-runner)). Everything it stores goes into one data folder next to it.

## Requirements

- **Docker.** Docker Desktop on Windows or macOS; Docker Engine with the Compose v2 plugin (`docker compose`) on Linux. On Windows, Docker Desktop with the WSL 2 backend.
- **git**, to get YAAP and to update it.
- **About 10 GB of disk** for the images, plus room for your projects and backups.
- **At least one model connection** (see [Connect a model](#connect-a-model)): a Claude subscription, a ChatGPT plan, an Anthropic or OpenAI API key, an Ollama server, or an OpenAI-compatible endpoint.

## Install with the setup script

```bash
git clone https://github.com/jelvodev/yaap.git
cd yaap
```

Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

macOS or Linux:

```bash
./scripts/setup.sh
```

The script:

1. checks that Docker and Compose run;
2. creates `.env` from `.env.example` with a new master key, unless `.env` already exists (it never changes an existing one);
3. builds and starts YAAP with `docker compose up -d --build`;
4. waits until <http://127.0.0.1:8080> answers;
5. asks for YAAP's password (twice, not shown while you type) when none is set yet. Press Enter to skip it and set the password in the browser instead;
6. shows how to connect a model.

**Save the master key.** Open `.env`, copy the value of `YAAP_MASTER_KEY` and keep it in a password manager or another safe place outside the data folder. It encrypts every key and secret YAAP stores. Without it, those values and the ones in your backups cannot be read.

## Install by hand

1. Copy `.env.example` to `.env`.
2. Generate a master key and put it in `.env` as `YAAP_MASTER_KEY`. Any of these prints one:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   head -c 32 /dev/urandom | base64 | tr '+/' '-_'
   ```

3. Start YAAP:

   ```bash
   docker compose up -d --build
   ```

4. Open <http://127.0.0.1:8080>, set a password on the first-run screen, and follow [the first start](#the-first-start).

## Signing in

YAAP asks for a password before it shows anything. There is one account; every browser signs in with the same password, and stays signed in until it signs out or is unused for 30 days.

- **First run.** Without a password, the browser shows *Set a password for YAAP*. The first person to open YAAP sets it, so set it right after installing or updating.
- **Forgotten password.** From the YAAP folder: `docker compose exec -it yaap python -m app.scripts.set_password --reset`. It asks twice and signs every browser out.
- **Signing out.** *Sign out* at the top right; Settings › Account › Access also signs out every other browser and changes the password.
- **Still only this computer.** YAAP listens on `127.0.0.1` only. The password is a second lock, not an invitation to open the port to a network.

## The first start

After the password, a new installation opens the first start: a short guide through what YAAP needs before the first conversation. Each step can be skipped, and *Finish later* leaves it until the next visit.

1. **A model connection**: an API key or a subscription sign-in (below), tested before it is stored.
2. **The environment and a first project**: keep or rename *Private* and *Work*, choose where to start, and create a project, empty or cloned from a git link.
3. **Backups**: where they go and how often.
4. **YAAP's starter**: standards, skills, commands and agents for a careful, test-first way of working, in packs you choose ([more](blueprint-packages.md#yaaps-starter)).

An installation that already has a connection or a project does not see it; Settings › System › First start has *Run the first start again*.

## Connect a model

Connections are set in **Settings › Connections › Providers**, at Global and overridable per environment; API keys are added per environment (switch the level at the top of the Settings rail). You can use several side by side.

| Connection | What you need | How |
|---|---|---|
| Claude through Claude Code (default for Claude) | A Claude Pro or Max subscription | `docker compose exec -it yaap claude auth login`, open the link, paste the code back |
| Anthropic API key | A key from the Anthropic Console (billed per use) | Settings › Connections › Providers: set the Claude connection to *Anthropic API key*, then add the key in the environment |
| OpenAI API key (default for OpenAI) | A key from the OpenAI platform | Settings › Connections › Providers: add the key in the environment |
| OpenAI through Codex | A ChatGPT plan | `docker compose exec -it yaap codex login --device-auth`, then set the OpenAI connection to *Codex* |
| Ollama | An Ollama server, for example on this computer | Settings › Connections › Providers at the Global level: set the Ollama address, for example `http://host.docker.internal:11434` |
| OpenAI-compatible endpoint | OpenRouter, LM Studio, vLLM or another server with an OpenAI-style API | Settings › Connections › Providers at the Global level, *OpenAI-compatible endpoints*: add a name and the base URL including `/v1`, for example `https://openrouter.ai/api/v1` or `http://host.docker.internal:1234/v1`. No key is sent yet, so use a server that needs none |

YAAP never handles subscription credentials itself: Claude Code and Codex keep their own sign-in in the data folder (`data/claude-code`, `data/codex`), and one sign-in serves every environment. Keys are tested before they are stored and are never shown again.

Set a spending limit at each provider's console as well; YAAP's budgets count only what it can see.

## Where your data lives

By default the data folder is `./data` next to `docker-compose.yml`. To put it elsewhere, set `YAAP_DATA_HOST` in `.env`, for example `YAAP_DATA_HOST=D:/YAAP-data`. It holds the database, the Blueprint, the project folders (`data/workspace`), attachments, knowledge, sign-ins and backups. You can open the project folders and the Blueprint in an editor on your computer; YAAP picks up changes made there.

## The speech model

Speaking to YAAP (the microphone in the chat) uses Whisper, run by YAAP itself on the CPU, so recordings never leave your computer. The model is not in the image: the first click on the microphone downloads it once into the data folder, at `data/models/whisper/<model>/`:

| Model | When | Size |
|---|---|---|
| `small` (multilingual) | Reply language empty or not English | 486 MB |
| `small.en` (English only) | Reply language English | 486 MB |

The files come from a pinned revision of `Systran/faster-whisper-small` (or `-small.en`) on Hugging Face and are checked against their SHA-256; a download that does not match is thrown away. A transcription needs about 500 MB of memory while the model is loaded; YAAP unloads it after 15 minutes without use. Backups leave the models out. On a computer without internet access, put the four files (`config.json`, `model.bin`, `tokenizer.json`, `vocabulary.txt`) of that revision in the folder yourself. To free the space, remove the folder; it is downloaded again on the next use.

## Personal Docker settings

To add something to `docker-compose.yml` for your own computer only, such as a backup folder, copy `docker-compose.override.example.yml` to `docker-compose.override.yml` and change it. Docker Compose merges it by itself, and git ignores it.

## Updating

```bash
git pull
docker compose up -d --build
```

Before you update, read [CHANGELOG.md](../CHANGELOG.md): each release lists what changed and, under **Action needed**, what you must do yourself. `GET /api/v1/version` tells which version is running.

The database is migrated automatically on start. Your data folder and `.env` are not touched. The Codex version is pinned and changes with YAAP's updates. Claude Code comes from Anthropic's stable channel when the image is built; to get its newest version, rebuild without the cache: `docker compose build --no-cache yaap`, then `docker compose up -d`.

## Backups

**Settings › System › Backup** makes a zip of the database, the Blueprint, attachments, knowledge, plugins and project folders, every day at 03:00 by default. Add a destination on another disk or a network share there (see [configuration.md](configuration.md#backups)). Restore is in the same place. The master key and the Claude Code and Codex sign-ins are not in a backup. See [usage.md](usage.md#backup-and-restore), including the manual restore for when YAAP does not start.

## Let YAAP work on this computer

By default the agent works in its environment's runner (see [The runner](#the-runner)), on the project folders under the data folder. It can also work in folders of the computer that runs YAAP, over SSH as a separate user `yaap`.

1. In **Settings › Targets** (per environment) add a computer. The defaults (`yaap@host.docker.internal:22`) are this computer. List the folders the agent may work in, such as `C:\Users\you\Projects`.
2. Open the target, choose **Standard user** (may change only those folders; recommended) or **Administrator**, and read the PowerShell script it shows. Run it in PowerShell as administrator. It installs OpenSSH Server, creates the user `yaap` that signs in with YAAP's key only, and opens port 22 only for Docker's networks.
3. Press **Test**. YAAP shows the computer's host key fingerprint. Compare it with the output of `ssh-keygen -lf $env:ProgramData\ssh\ssh_host_ed25519_key.pub` and press **Confirm this host key**. The test then shows Windows, the shell and the rights.
4. Choose the target in a project's settings (*Where the agent works*, with the project's folder on the computer), or pick it in the chat before the first message of a conversation outside a project.

Every change on the computer asks first and cannot be rolled back in YAAP. *Allow full access* on the target lets a conversation with the access choice *full* work without asking there; the header then shows a red *Full control*. To stop, remove the target in YAAP, and on the computer remove the user `yaap` and the firewall rule `YAAP-SSH` (`Remove-LocalUser yaap; Remove-NetFirewallRule -Name YAAP-SSH`).

On Linux or macOS, put the public key from the target screen in `~/.ssh/authorized_keys` of a user made for this instead of running the script.

## The runner

Everything the agent runs (commands, background processes such as a dev server, MCP servers over stdio and its browser) runs in the *runner* of its environment: a Debian container with common build tools (gcc, make, Python, Node.js, git) and Chromium, in which the agent may install what it needs with `sudo`. The runner holds only that environment's project folders (as `/workspace`, so the files stay in YAAP's data folder) and cannot reach YAAP itself, so a command cannot read YAAP's database, master key, sign-ins or another environment's projects. It has internet access unless you switch that off in Settings › Targets. It starts on the first command and stops after an hour without use. See [architecture.md](architecture.md#1a-where-commands-run-the-runner-and-its-networks).

`docker compose up -d --build` builds and starts it with YAAP. Next to `yaap` and `searxng` you then see:

- `yaap-sandbox`, a small helper that alone starts, stops and removes runner and sandbox containers, and only those with YAAP's sandbox label. YAAP talks to it with a token the helper writes into a volume only the two share; YAAP itself never gets the Docker socket.
- `docker-proxy` (`tecnativa/docker-socket-proxy`), which gives the helper the container and image parts of the Docker API and nothing else (no networks, volumes, exec or system calls). Only the helper can reach it.
- `sandbox-image`, which only builds the image `yaap-sandbox:local` and exits at once; that is expected.
- per environment that ran a command, a container `<project>-sandbox-e<number>` (and `<project>-sandbox-p<number>` per project that chose its own sandbox), where `<project>` is the Compose project name, normally `yaap`.

Docker gives the proxy the socket of your Docker engine, which is as much as administrator rights on the computer; that is why the helper is a separate container behind a filter. On Docker Desktop for Windows the helper turns the workspace folder it sees (such as `C:\YAAP\data\workspace`) into the path Docker's engine uses (`/run/desktop/mnt/host/c/YAAP/data/workspace`); if your setup differs, set `YAAP_SANDBOX_WORKSPACE_HOST` for the `yaap-sandbox` service in `docker-compose.override.yml`.

An existing installation that still has `COMPOSE_PROFILES=sandbox` in `.env` can remove that line; the helper and the proxy always start. Packages installed in YAAP's own container (`pip install --user`, global npm packages) are not in the runner: let the agent install them there, or add them as [extra packages](configuration.md#extra-packages-for-the-runner).

To remove runners and sandboxes by hand: `docker rm -f $(docker ps -aq --filter label=org.yaap.sandbox=1)`; YAAP makes a runner again on the next command.

## A sandbox per project

A project can also let its agent work in a container of its own instead of the environment's runner, the *sandbox*: the same image, but with only the project folder as `/workspace` and without network unless you switch it on for that project. Choose **Sandbox** under *Where the agent works* in a project's settings (see [usage.md](usage.md#the-project-rail)). After a `git pull` and `docker compose up -d --build`, a sandbox is made again from the new image on its next use; *Rebuild* does it at once.

## Moving to another computer

1. Make a backup in Settings › System › Backup and download it, or copy the whole data folder while YAAP is stopped (`docker compose down`).
2. Install YAAP on the new computer and put the **same** `YAAP_MASTER_KEY` in its `.env`.
3. Upload the backup in Settings › System › Backup and restore it, or put the copied data folder in place before the first start.
4. Sign in to Claude Code and Codex again.

## Stopping and uninstalling

- Stop: `docker compose down`. Start again: `docker compose up -d`.
- Runners and sandboxes are started by YAAP's helper, not by Compose, so `docker compose down` leaves them; they stop by themselves after an hour without use. To remove them at once: `docker rm -f $(docker ps -aq --filter label=org.yaap.sandbox=1)`.
- Uninstall: remove the runners and sandboxes as above, then `docker compose down --rmi local`, then remove the YAAP folder. The data folder goes with it unless you moved it with `YAAP_DATA_HOST`; keep a backup first if you may want it back.

## Troubleshooting

| Problem | What to do |
|---|---|
| The page does not load | `docker compose ps` shows whether `yaap`, `searxng`, `yaap-sandbox` and `docker-proxy` run; `docker compose logs yaap` shows why YAAP stopped. |
| The password is forgotten | `docker compose exec -it yaap python -m app.scripts.set_password --reset` sets a new one and signs every browser out. |
| "Too many wrong passwords" | After five wrong passwords within a minute YAAP waits; try again a minute later. |
| A script gets 401 | The REST API needs a token: make one in Settings › Account › Access and send it as `Authorization: Bearer <token>`. `GET /api/v1/health` stays open. |
| "YAAP_MASTER_KEY is missing or invalid" | `.env` lacks the key or it was changed. Put back the key you saved; a new key cannot read existing keys and secrets. |
| Claude Code says it is not signed in | Run `docker compose exec -it yaap claude auth login` again. Settings › Connections › Providers shows the status. |
| A model is shown with "(no key)" | That connection has no key or sign-in in the active environment; add one in Settings › Connections › Providers. |
| Web search finds nothing | `docker compose logs searxng`; the search engines SearXNG asks may limit requests for a while. |
| The first build fails while downloading | Run `docker compose up -d --build` again; downloads are checked against pinned hashes and resume from the cache. |
| The microphone says the speech model could not be downloaded | YAAP needs to reach `huggingface.co` once. Click the microphone to try again, or place the files by hand (see [The speech model](#the-speech-model)). |
| The microphone cannot record | Open YAAP at `http://localhost:8080` or `http://127.0.0.1:8080` (browsers allow the microphone only there or over https) and allow the microphone in the browser's site settings. |
| A command says the runner cannot start, or the project settings say the sandbox helper is not running | Run `docker compose up -d --build` in YAAP's folder. `docker compose logs yaap-sandbox` shows whether the helper found Docker, its workspace folder and the image `yaap-sandbox:local`. |
| A command cannot find a tool | Commands run in the environment's runner, not in YAAP's container; install it in the runner (`sudo apt-get install …`, `npm install -g …`) or add it as an [extra package](configuration.md#extra-packages-for-the-runner). |
| Port 8080 is in use | Use another port in `docker-compose.override.yml`: under `services: yaap:` put `ports: !override ["127.0.0.1:8082:8080", "127.0.0.1:8081:8081"]` (Compose 2.24 or later), then open <http://127.0.0.1:8082>. Port 8081 is the preview (`YAAP_PREVIEW_PORT`); when that one is in use, set another in `.env` and name it twice in the second entry. |
