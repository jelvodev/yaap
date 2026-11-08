#!/usr/bin/env bash
# Set up and start YAAP on macOS or Linux: creates .env with a new master key (once), builds and starts the
# containers, waits until YAAP answers and asks for YAAP's password when none is set yet. Run it from anywhere:
# ./scripts/setup.sh
set -euo pipefail

cd "$(dirname "$0")/.."
URL="http://127.0.0.1:8080"

say() { printf '%s\n' "$*"; }
fail() { printf 'Setup stopped: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop (macOS) or Docker Engine (Linux): https://docs.docker.com/get-docker/"
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start Docker and run this script again."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing ('docker compose'). Update Docker: https://docs.docker.com/compose/install/"

if [ -f .env ]; then
  say "Using the existing .env (it is never changed by this script)."
else
  [ -f .env.example ] || fail ".env.example is missing; is this the YAAP folder?"
  # A Fernet key: 32 random bytes as URL-safe base64.
  key="$(head -c 32 /dev/urandom | base64 | tr -d '\n' | tr '+/' '-_')"
  cp .env.example .env
  if grep -q '^YAAP_MASTER_KEY=' .env; then
    # Portable in-place edit (BSD and GNU sed differ in -i).
    sed "s|^YAAP_MASTER_KEY=.*|YAAP_MASTER_KEY=${key}|" .env > .env.tmp && mv .env.tmp .env
  else
    printf '\nYAAP_MASTER_KEY=%s\n' "$key" >> .env
  fi
  chmod 600 .env
  say "Created .env with a new master key."
  say ""
  say "  IMPORTANT: keep a copy of YAAP_MASTER_KEY from .env somewhere safe, outside the data folder"
  say "  (a password manager is a good place). Without it, a backup's keys and secrets cannot be read."
  say ""
fi

say "Building and starting YAAP. The first build downloads a few GB and takes several minutes."
docker compose up -d --build

say "Waiting for YAAP to answer at $URL ..."
for _ in $(seq 1 90); do
  if curl -fsS "$URL/api/v1/health" >/dev/null 2>&1; then
    say ""
    say "YAAP is running: $URL"
    if curl -fsS "$URL/api/v1/auth/state" 2>/dev/null | grep -q '"password_set":false'; then
      say ""
      say "YAAP asks for a password in the browser. Choose it now (at least 10 characters),"
      say "or press Enter to set it on the first visit in the browser instead."
      password=""
      while true; do
        read -rsp "Password: " password; printf '\n'
        [ -z "$password" ] && break
        read -rsp "The same password again: " again; printf '\n'
        [ "$password" = "$again" ] && break
        say "The two passwords differ; try again."
      done
      if [ -n "$password" ]; then
        # On standard input, so the password never shows on a command line.
        printf '%s\n' "$password" | docker compose exec -T yaap python -m app.scripts.set_password --stdin \
          || say "The password was not set; set it in the browser instead."
      else
        say "Open $URL and set the password there first."
      fi
      unset password again
    fi
    say ""
    say "Next: connect a model. Either"
    say "  - sign in to Claude Code with your Claude subscription:   docker compose exec -it yaap claude auth login"
    say "  - sign in to Codex with your ChatGPT plan:                docker compose exec -it yaap codex login --device-auth"
    say "  - or add an API key (Anthropic, OpenAI) or an Ollama address in Settings > Connections."
    say "See docs/install.md for details."
    exit 0
  fi
  sleep 2
done
fail "YAAP did not answer within three minutes. See the log with: docker compose logs yaap"
