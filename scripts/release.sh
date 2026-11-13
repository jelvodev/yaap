#!/usr/bin/env bash
# Release YAAP (for the maintainer): sets the version everywhere, rolls the changelog, commits, tags the release and
# asks before pushing. Run it from anywhere, on an up-to-date, clean main: ./scripts/release.sh 0.6.0
set -euo pipefail

cd "$(dirname "$0")/.."

say() { printf '%s\n' "$*"; }
fail() { printf 'Release stopped: %s\n' "$*" >&2; exit 1; }

# The workflow .github/workflows/release.yml publishes the GitHub Release when the tag arrives; the public API shows
# when it is there, so this needs no token. Unauthenticated, GitHub allows 60 requests an hour: one every 30 seconds.
wait_for_release() {
  local remote repo url
  remote="$(git remote get-url origin)"
  repo="$(printf '%s' "$remote" | sed -E 's#^(https://([^@/]*@)?github\.com/|git@github\.com:)##; s#\.git$##')"
  if [ "$repo" = "$remote" ]; then
    say "origin is not on github.com, so there is no GitHub Release to wait for."
    return 0
  fi
  url="https://api.github.com/repos/$repo/releases/tags/$tag"
  say "Waiting for the GitHub Release of $tag (Actions: https://github.com/$repo/actions)..."
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then
      say "Released: https://github.com/$repo/releases/tag/$tag"
      return 0
    fi
    sleep 30
  done
  say "The Release of $tag did not appear within 30 minutes; see https://github.com/$repo/actions."
  return 1
}

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
  wait_for_release
else
  say "Not pushed. Push later with: git push origin main $tag"
fi
