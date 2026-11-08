// Tests of which actions the Changes panel shows per repository state, and the Sync label (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { changesActions, commitLabel, stateLetter, syncLabel, syncText } from "./changesState.ts";

const REMOTE = "https://github.com/owner/repository.git";

function repository(facts = {}) {
  const { changed = 0, ahead = 0, behind = 0, upstream = "origin/main", remote = REMOTE, commits = true } = facts;
  return {
    changes: Array.from({ length: changed }, (_, index) => ({ path: `file${index}`, state: "modified" })),
    ahead,
    behind,
    upstream,
    remote_url: remote,
    has_commits: commits,
  };
}

const NO_REMOTE = { remote: null, upstream: null };
const UNBORN = { upstream: null, commits: false };

// name, state, [commit and review, sync, fetch, pull, push, remote hint], Sync label
const STATES = [
  ["nothing changed", {}, [false, "secondary", true, true, true, false], "Sync · ↑0 · ↓0"],
  ["changes", { changed: 2 }, [true, "secondary", true, true, true, false], "Sync · ↑0 · ↓0"],
  ["commits ahead", { ahead: 1 }, [false, "primary", true, true, true, false], "Sync · ↑1 · ↓0"],
  ["behind", { behind: 3 }, [false, "primary", true, true, true, false], "Sync · ↑0 · ↓3"],
  ["ahead and behind", { ahead: 1, behind: 2 }, [false, "primary", true, true, true, false], "Sync · ↑1 · ↓2"],
  ["changes and ahead", { changed: 1, ahead: 1 }, [true, "secondary", true, true, true, false], "Sync · ↑1 · ↓0"],
  ["never pushed", { upstream: null }, [false, "primary", true, false, true, false], "Sync · not pushed yet"],
  ["no commits yet", { ...UNBORN, changed: 1 }, [true, "secondary", true, false, false, false], "Sync"],
  ["no remote", NO_REMOTE, [false, "hidden", false, false, false, true], "No remote"],
  ["no remote, changes", { ...NO_REMOTE, changed: 4 }, [true, "hidden", false, false, false, true], "No remote"],
];

for (const [name, facts, [commit, sync, fetch, pull, push, remoteHint], label] of STATES) {
  test(`${name}: the actions and the Sync label`, () => {
    const state = repository(facts);
    assert.deepEqual(changesActions(state), { commit, review: commit, sync, fetch, pull, push, remoteHint });
    assert.equal(syncLabel(state), label);
  });
}

test("syncText says where the branch stands in words", () => {
  assert.equal(syncText(repository()), "up to date");
  assert.equal(syncText(repository({ ahead: 2, behind: 1 })), "2 to push · 1 to pull");
  assert.equal(syncText(repository({ upstream: null })), "not pushed yet");
  assert.equal(syncText(repository({ remote: null })), "no remote");
});

test("stateLetter gives git's letter for each state, commitLabel counts the files", () => {
  const states = ["modified", "untracked", "added", "deleted", "renamed", "conflict", "odd"];
  assert.deepEqual(states.map(stateLetter), ["M", "A", "A", "D", "R", "C", "?"]);
  assert.equal(commitLabel(1), "Commit 1 file");
  assert.equal(commitLabel(5), "Commit 5 files");
});
