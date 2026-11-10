#!/usr/bin/env bash
# Check YAAP's pinned packages for known vulnerabilities: the frontend's npm packages, the Python lock files of YAAP
# and the sandbox helper, the pinned Claude Code and Codex, and the npm packages installed globally in the images.
# Debian's own packages are not checked. Needs npm and uv (uvx). Exits non-zero when a check finds something or cannot run. Run it from anywhere:
# ./scripts/audit.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PIP_AUDIT="pip-audit==2.10.1"

say() { printf '%s\n' "$*"; }
fail() { printf 'Audit stopped: %s\n' "$*" >&2; exit 2; }

command -v npm >/dev/null 2>&1 || fail "npm is not installed: https://nodejs.org/"
command -v uvx >/dev/null 2>&1 || fail "uv (uvx) is not installed: https://docs.astral.sh/uv/"

# The global npm packages of the images, by the Dockerfile ARG that pins their version.
GLOBAL_PACKAGES=(
  "docker/sandbox/Dockerfile PLAYWRIGHT_MCP_VERSION @playwright/mcp"
  "docker/sandbox/Dockerfile PYRIGHT_VERSION pyright"
  "docker/sandbox/Dockerfile TYPESCRIPT_LANGUAGE_SERVER_VERSION typescript-language-server"
  "docker/sandbox/Dockerfile TYPESCRIPT_VERSION typescript"
)

results=()
failed=0
record() {
  # record <name> <exit code>
  if [ "$2" -eq 0 ]; then
    results+=("ok        $1")
  else
    results+=("FAILED    $1 (findings, or the check could not run)")
    failed=1
  fi
}

arg_value() {
  # arg_value <Dockerfile> <ARG name>: the default value of that ARG.
  sed -n "s/^ARG $2=\([^[:space:]]*\).*/\1/p" "$1" | tr -d '\r' | head -n 1
}

say "== Frontend (frontend/package-lock.json)"
status=0
(cd frontend && npm audit) || status=$?
record "frontend npm packages" "$status"

say ""
say "== Claude Code and Codex (tools/package-lock.json)"
status=0
(cd tools && npm audit) || status=$?
record "Claude Code and Codex npm packages" "$status"

for lock in backend/requirements.lock docker/sandbox-helper/requirements.lock; do
  say ""
  say "== Python ($lock)"
  status=0
  uvx "$PIP_AUDIT" --require-hashes --disable-pip -r "$lock" || status=$?
  record "Python packages in $lock" "$status"
done

say ""
say "== Global npm packages in the images"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
deps=""
for entry in "${GLOBAL_PACKAGES[@]}"; do
  read -r dockerfile arg package <<<"$entry"
  version="$(arg_value "$dockerfile" "$arg")"
  [ -n "$version" ] || fail "no ARG $arg in $dockerfile"
  say "$package@$version ($dockerfile)"
  deps="$deps${deps:+,}\"$package\": \"$version\""
done
printf '{"name": "yaap-image-globals", "private": true, "dependencies": {%s}}\n' "$deps" > "$work/package.json"
# A global install has no lock file, so this resolves the dependencies as a build today would.
status=0
(cd "$work" && npm install --package-lock-only --ignore-scripts --no-fund --no-audit >/dev/null && npm audit) \
  || status=$?
record "global npm packages in the images" "$status"

say ""
say "== Summary"
for line in "${results[@]}"; do say "$line"; done
exit "$failed"
