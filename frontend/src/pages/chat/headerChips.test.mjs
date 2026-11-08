// Tests of the chat header's chips (npm run test:unit). Node strips the types of headerChips.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { changeChip, projectChipLabel } from "./headerChips.ts";

const CHANGE_STATES = [
  // changed files, commits ahead, label (null: no chip), kind
  [0, 0, null, null],
  [5, 0, "5 changed · Review", "changes"],
  [1, 0, "1 changed · Review", "changes"],
  [0, 1, "1 to push · Sync", "push"],
  [0, 3, "3 to push · Sync", "push"],
  [5, 1, "5 changed · 1 to push", "both"],
  [-1, 0, null, null],
];

for (const [changed, ahead, label, kind] of CHANGE_STATES) {
  test(`the change chip for ${changed} changed and ${ahead} ahead is ${label ?? "hidden"}`, () => {
    const chip = changeChip(changed, ahead);
    if (label === null) assert.equal(chip, null);
    else {
      assert.equal(chip?.label, label);
      assert.equal(chip?.kind, kind);
    }
  });
}

test("the change chip's tooltip counts files and commits in words", () => {
  assert.match(changeChip(1, 0).title, /^1 file has uncommitted changes/);
  assert.match(changeChip(2, 3).title, /2 files have uncommitted changes and 3 commits are not pushed/);
});

const PLACES = [
  // project, branch, target, full control, label
  ["TESTPROJECT_Yaap", "main", null, false, "TESTPROJECT_Yaap · main"],
  ["TESTPROJECT_Yaap", null, null, false, "TESTPROJECT_Yaap"],
  ["TESTPROJECT_Yaap", "main", "devbox", false, "TESTPROJECT_Yaap · main · on devbox"],
  ["TESTPROJECT_Yaap", "main", "devbox", true, "TESTPROJECT_Yaap · main · full control on devbox"],
  [null, "main", "devbox", false, "On devbox"],
  [null, null, "devbox", true, "Full control on devbox"],
  [null, null, null, false, null],
];

for (const [project, branch, target, fullControl, label] of PLACES) {
  test(`the project chip reads ${label ?? "nothing"}`, () => {
    assert.equal(projectChipLabel({ project, branch, target, fullControl }), label);
  });
}
