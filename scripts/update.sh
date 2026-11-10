#!/usr/bin/env bash
# Update YAAP on macOS or Linux to the newest release (or to the newest commit of main), with a backup first and a way
# back when the new version does not start. Run it from anywhere:
#   ./scripts/update.sh [--channel stable|main] [--yes]     update
#   ./scripts/update.sh --rollback [--yes]                  go back to the version before the last update
# Needs a clean checkout: it stops and names the files when you changed a tracked file. Your data, .env and
# docker-compose.override.yml are never touched. The decisions (busy, backup, rollback, changelog) are Python, in
# the image: python -m app.scripts.update; this script only runs git and Docker.
set -euo pipefail

cd "$(dirname "$0")/.."
URL="http://127.0.0.1:8080"
CHANNEL="stable"
YES=0
ROLLBACK=0
IMAGES="yaap yaap-sandbox yaap-sandbox-helper"

say() { printf '%s\n' "$*"; }
fail() { printf 'Update stopped: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --channel) [ $# -ge 2 ] || fail "--channel needs stable or main."; CHANNEL="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --rollback) ROLLBACK=1; shift ;;
    *) fail "Unknown option '$1'. Use --channel stable|main, --yes or --rollback." ;;
  esac
done
case "$CHANNEL" in stable|main) ;; *) fail "The channel is stable or main, not '$CHANNEL'." ;; esac

# Where the data folder is on this computer (YAAP_DATA_HOST in .env or the environment, else ./data).
DATA="${YAAP_DATA_HOST:-}"
if [ -z "$DATA" ] && [ -f .env ]; then
  DATA="$(sed -n 's/^YAAP_DATA_HOST=//p' .env | tail -1 | tr -d '\r"'"'")"
fi
DATA="${DATA:-./data}"
STATE="$DATA/update-state.json"
LOG="$DATA/update.log"

ask() {
  # ask "question" [default y|n]: with --yes the answer is yes; without a terminal it is the default.
  local default="${2:-y}" reply=""
  [ "$YES" = 1 ] && return 0
  if [ "$default" = y ]; then printf '%s [Y/n] ' "$1"; else printf '%s [y/N] ' "$1"; fi
  read -r reply || reply=""
  reply="${reply:-$default}"
  case "$reply" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

is_running() { curl -fsS --max-time 5 "$URL/api/v1/health" >/dev/null 2>&1; }
json_get() { sed -n "s/.*\"$1\": *\"\([^\"]*\)\".*/\1/p" "$2" | head -1; }
file_version() { sed -n 's/^VERSION = "\(.*\)"/\1/p' backend/app/version.py | tr -d '\r'; }
running_version() { curl -fsS --max-time 5 "$URL/api/v1/version" 2>/dev/null | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'; }
log_line() {
  { mkdir -p "$DATA" && printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; } 2>/dev/null \
    || say "Could not write $LOG."
}

# The Python steps run in the image: in the container when YAAP runs, else in a throwaway one on the current image.
step() {
  if is_running; then docker compose exec -T yaap python -m app.scripts.update "$@"
  else docker compose run --rm --no-deps -T yaap python -m app.scripts.update "$@"; fi
}

wait_for_health() {
  say "Waiting for YAAP to answer at $URL ..."
  for _ in $(seq 1 90); do
    is_running && return 0
    sleep 2
  done
  return 1
}

# ---- Checks -----------------------------------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop (macOS) or Docker Engine (Linux): https://docs.docker.com/get-docker/"
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start Docker and run this script again."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing ('docker compose'). Update Docker: https://docs.docker.com/compose/install/"
command -v git >/dev/null 2>&1 || fail "git is not installed."
[ -f docker-compose.yml ] && [ -f backend/app/version.py ] && [ -d .git ] || fail "This is not the YAAP folder (a git checkout with docker-compose.yml)."
changed="$(git status --porcelain --untracked-files=no)"
if [ -n "$changed" ]; then
  say "These tracked files have changes:" >&2
  printf '%s\n' "$changed" | sed 's/^/  /' >&2
  fail "Commit or undo them first. If you change YAAP itself, update with git instead of this script."
fi
OLD_COMMIT="$(git rev-parse HEAD)"
OLD_BRANCH="$(git symbolic-ref --short -q HEAD || true)"

# ---- Going back --------------------------------------------------------------------------------------------------
# Old code, the previous images, the data when the database moved on, then start without building.
go_back() {
  local commit="$1" branch="$2" backup="$3"
  # Check the backup before anything changes: without it a database that moved on cannot be restored.
  if [ -n "$backup" ] && [ ! -f "$DATA/backups/$backup" ]; then
    fail "The backup $DATA/backups/$backup does not exist, so nothing was changed. Restore the data from another backup as described in docs/usage.md (Backup and restore)."
  fi
  say "Going back to ${commit:0:12} ..."
  # go_back runs in an '&&' list, where 'set -e' does not apply: every step names its own failure.
  if [ -n "$branch" ]; then
    git checkout "$branch" || fail "git could not check out $branch."
    git reset --keep "$commit" || fail "git could not go back to the old commit."
  else
    git checkout --detach "$commit" || fail "git could not go back to the old commit."
  fi
  for image in $IMAGES; do
    if docker image inspect "$image:previous" >/dev/null 2>&1; then docker tag "$image:previous" "$image:local"; fi
  done
  if [ -n "$backup" ]; then
    docker compose run --rm --no-deps -T yaap python -m app.scripts.update rollback "$backup" \
      || fail "The old version could not judge the database. Your backup is $DATA/backups/$backup; restore it as described in docs/usage.md (Backup and restore)."
  fi
  docker compose up -d
  if wait_for_health; then
    say "YAAP is running again on the previous version: $URL"
    log_line "rolled back to ${commit:0:12}${backup:+ (backup $backup)}"
    rm -f "$STATE"
  else
    fail "YAAP did not answer within three minutes after going back. See the log with: docker compose logs yaap"
  fi
}

if [ "$ROLLBACK" = 1 ]; then
  if [ -f "$STATE" ]; then
    back_commit="$(json_get old_commit "$STATE")"; back_branch="$(json_get old_branch "$STATE")"
    back_backup="$(json_get backup "$STATE")"
  else
    last="$(grep ' updated ' "$LOG" 2>/dev/null | tail -1 || true)"
    [ -n "$last" ] || fail "There is no update to go back from ($STATE and $LOG have none)."
    back_commit="$(printf '%s' "$last" | sed -n 's/.* from commit \([0-9a-f]*\).*/\1/p')"
    back_branch="$(printf '%s' "$last" | sed -n 's/.* branch \([^ ]*\).*/\1/p')"
    [ "$back_branch" = "-" ] && back_branch=""
    back_backup="$(printf '%s' "$last" | sed -n 's/.* backup \(yaap-backup-[0-9-]*\.zip\).*/\1/p')"
  fi
  [ -n "$back_commit" ] || fail "The previous version is not recorded."
  say "This goes back to commit ${back_commit:0:12}. Work done since that update is lost when the database"
  say "moved on: it is restored from ${back_backup:-no backup}, and what it replaces stays in $DATA/.restore-previous."
  ask "Go back?" n || fail "Nothing was changed."
  go_back "$back_commit" "$back_branch" "$back_backup"
  exit 0
fi

# ---- Which version -----------------------------------------------------------------------------------------------
if [ -f "$STATE" ]; then
  say "An earlier update did not finish (it is recorded in $STATE)."
  ask "Go back to the version before it?" y && { go_back "$(json_get old_commit "$STATE")" "$(json_get old_branch "$STATE")" "$(json_get backup "$STATE")"; exit 0; }
  fail "Run this script with --rollback when you want to go back, or remove $STATE if the update is fine."
fi

say "Looking for a new version ..."
git fetch --tags --force --quiet origin || fail "git could not reach the remote ('git fetch'). Check your connection."
CURRENT="$(running_version || true)"
[ -n "$CURRENT" ] || CURRENT="$(file_version)"

if [ "$CHANNEL" = stable ]; then
  TAG="$(git tag --list 'v*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true)"
  [ -n "$TAG" ] || fail "No release (a tag like v0.6.0) was found."
  NEW="${TAG#v}"; TARGET="$TAG"
  newest="$(printf '%s\n%s\n' "$CURRENT" "$NEW" | sort -V | tail -1)"
  if [ "$CURRENT" = "$NEW" ] || [ "$newest" != "$NEW" ]; then
    say "YAAP $CURRENT is the newest release."; exit 0
  fi
  CHANGELOG_TO="$NEW"
else
  git fetch --quiet origin main || fail "git could not fetch main."
  TARGET="origin/main"
  [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ] || { say "YAAP is on the newest commit of main."; exit 0; }
  NEW="$(git show origin/main:backend/app/version.py | sed -n 's/^VERSION = "\(.*\)"/\1/p' | tr -d '\r')"
  CHANGELOG_TO="main"
fi

# ---- Show what comes ---------------------------------------------------------------------------------------------
say ""
say "Current version: $CURRENT"
say "New version:     $NEW ($CHANNEL, $(git rev-parse --short "$TARGET"))"
say ""
git show "$TARGET:CHANGELOG.md" | step changelog "$CURRENT" "$CHANGELOG_TO" \
  || { say "The changelog could not be formatted; this is the start of CHANGELOG.md of the new version:"; git show "$TARGET:CHANGELOG.md" | head -60; }
say ""
ask "Update YAAP from $CURRENT to $NEW?" y || fail "Nothing was changed."

# ---- Busy? -------------------------------------------------------------------------------------------------------
WAS_RUNNING=0
if is_running; then
  WAS_RUNNING=1
  working="$(docker compose exec -T yaap python -m app.scripts.update busy)" || fail "YAAP could not say what is working."
  if [ -n "$working" ]; then
    say "YAAP is working on:"
    printf '%s\n' "$working" | sed 's/^/  /'
    say "Updating restarts YAAP and cuts this work off."
    [ "$YES" = 1 ] && fail "Run it again when this is done, or without --yes to go on anyway."
    ask "Update anyway?" n || fail "Nothing was changed."
  fi
fi

# ---- Backup ------------------------------------------------------------------------------------------------------
say "Making a backup ..."
BACKUP="$(step backup | tail -1)" || fail "The backup failed; nothing was changed."
printf '%s' "$BACKUP" | grep -Eq '^yaap-backup-[0-9]{8}-[0-9]{6}\.zip$' || fail "The backup step gave no backup name; nothing was changed."
say "Backup: $BACKUP"

# ---- Keep the old version ----------------------------------------------------------------------------------------
for image in $IMAGES; do
  if docker image inspect "$image:local" >/dev/null 2>&1; then docker tag "$image:local" "$image:previous"; fi
done
mkdir -p "$DATA" 2>/dev/null || true
printf '{"old_commit": "%s", "old_branch": "%s", "old_version": "%s", "new_version": "%s", "backup": "%s", "channel": "%s"}\n' \
  "$OLD_COMMIT" "$OLD_BRANCH" "$CURRENT" "$NEW" "$BACKUP" "$CHANNEL" > "$STATE" \
  || fail "$STATE cannot be written (is $DATA owned by another user? try sudo). Only a backup was made."

# ---- New code, build and start -----------------------------------------------------------------------------------
checked_out=1
if [ "$CHANNEL" = stable ]; then
  git checkout --detach "$TARGET" || checked_out=0
else
  { git checkout main && git pull --ff-only origin main; } || checked_out=0
fi
if [ "$checked_out" = 0 ]; then
  say "git could not check out the new version. Nothing was built." >&2
  if ask "Go back to $CURRENT?" y; then
    go_back "$OLD_COMMIT" "$OLD_BRANCH" "$BACKUP"
    exit 1
  fi
  fail "YAAP stays as it is. Go back later with: ./scripts/update.sh --rollback"
fi
export YAAP_COMMIT="$(git rev-parse --short HEAD)" YAAP_CHANNEL="$CHANNEL" CLI_REFRESH="$(date +%Y-%m)"
say "Building and starting YAAP $NEW. This takes a few minutes."

ok=0
if docker compose up -d --build && wait_for_health; then
  reported="$(running_version || true)"
  if [ "$reported" = "$NEW" ]; then ok=1; else say "YAAP answers, but reports version '$reported' instead of $NEW." >&2; fi
fi

if [ "$ok" = 1 ]; then
  log_line "updated $CURRENT -> $NEW from commit $OLD_COMMIT branch ${OLD_BRANCH:--} to $YAAP_COMMIT channel $CHANNEL backup $BACKUP"
  rm -f "$STATE"
  docker image prune -f --filter dangling=true --filter label=dev.yaap=true >/dev/null 2>&1 || true
  say ""
  say "YAAP is updated: $CURRENT -> $NEW ($URL)"
  say "The version before it stays as the :previous images; ./scripts/update.sh --rollback goes back."
  git show "$TARGET:CHANGELOG.md" | step changelog "$CURRENT" "$CHANGELOG_TO" 2>/dev/null | sed -n '1,/^What changed:/p' | sed '$d' || true
  exit 0
fi

say ""
say "The update did not work. The last 40 lines of the log:" >&2
docker compose logs --no-color --tail 40 yaap >&2 || true
say ""
if ask "Go back to $CURRENT?" y; then
  go_back "$OLD_COMMIT" "$OLD_BRANCH" "$BACKUP"
  exit 1
fi
fail "YAAP stays on $NEW. Go back later with: ./scripts/update.sh --rollback"
