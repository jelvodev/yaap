#!/usr/bin/env bash
# Release YAAP (for the maintainer): sets the version everywhere, rolls the changelog, commits, tags the release and
# asks before pushing. Run it from anywhere, on an up-to-date, clean main: ./scripts/release.sh 0.6.0
set -euo pipefail

cd "$(dirname "$0")/.."

say() { printf '%s\n' "$*"; }
fail() { printf 'Release stopped: %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || fail "usage: scripts/release.sh <version>, for example 0.6.0"
version="$1"
tag="v$version"
printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || fail "'$version' is not a version like 1.2.3."

[ "$(git branch --show-current)" = "main" ] || fail "check out main first."
[ -z "$(git status --porcelain)" ] || fail "the working copy has uncommitted changes."
git fetch origin main --tags --quiet || fail "git fetch failed."
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || fail "main is not equal to origin/main; pull or push first."
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && fail "the tag $tag already exists."

if [ -x backend/.venv/bin/python ]; then
  python="$PWD/backend/.venv/bin/python"
elif [ -x backend/.venv/Scripts/python.exe ]; then
  python="$PWD/backend/.venv/Scripts/python.exe"
else
  python="python"
fi

(cd backend && "$python" -m app.scripts.set_version "$version") || fail "the version was not set."
notes="$(cd backend && "$python" -m app.scripts.set_version --section "$version")" || fail "the changelog has no section."

git add -A
git commit --quiet -m "Set the version to $version"
git tag -a "$tag" -m "YAAP $version

$notes"
say ""
say "Committed and tagged $tag:"
git show --no-patch --format='%h %s' HEAD
say ""
say "$notes"
say ""
read -rp "Push main and $tag to origin now? [y/N] " answer
if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
  git push origin main "$tag"
  say "Pushed $tag."
else
  say "Not pushed. Push later with: git push origin main $tag"
fi
