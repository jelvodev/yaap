// Tests of the project rail's badges and of when it folds (npm run test:unit). Node strips the types of railItems.ts
// as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { RAIL_PANELS } from "./railPanels.ts";
import { RAIL_ITEMS, anyRailBadge, railBadge, railItemLabel, railMode } from "./railItems.ts";

const QUIET = { changedFiles: 0, previewRunning: false, openProposals: 0 };

test("every rail panel has a name and a title", () => {
  for (const panel of RAIL_PANELS) {
    assert.ok(RAIL_ITEMS[panel].label, panel);
    assert.ok(RAIL_ITEMS[panel].title, panel);
  }
});

test("no icon has a badge while nothing is changed, running or proposed", () => {
  for (const panel of RAIL_PANELS) assert.equal(railBadge(panel, QUIET), null, panel);
  assert.equal(anyRailBadge(RAIL_PANELS, QUIET), false);
});

const BADGES = [
  // panel, counts, expected badge
  ["changes", { changedFiles: 5 }, { kind: "count", text: "5", label: "5 changed files" }],
  ["changes", { changedFiles: 1 }, { kind: "count", text: "1", label: "1 changed file" }],
  ["changes", { changedFiles: 140 }, { kind: "count", text: "99+", label: "140 changed files" }],
  ["preview", { previewRunning: true }, { kind: "dot", label: "a process with a port runs" }],
  ["memory", { openProposals: 2 }, { kind: "count", text: "2", label: "2 open proposals" }],
  ["memory", { openProposals: 1 }, { kind: "count", text: "1", label: "1 open proposal" }],
];

for (const [panel, counts, expected] of BADGES) {
  test(`${panel} shows ${expected.label}`, () => {
    assert.deepEqual(railBadge(panel, { ...QUIET, ...counts }), expected);
    assert.equal(anyRailBadge(RAIL_PANELS, { ...QUIET, ...counts }), true);
  });
}

test("a count shows only on its own icon", () => {
  const counts = { changedFiles: 3, previewRunning: true, openProposals: 4 };
  const badged = RAIL_PANELS.filter((panel) => railBadge(panel, counts) !== null);
  assert.deepEqual(badged, ["changes", "preview", "memory"]);
});

test("the icon's name says what its badge says", () => {
  assert.equal(railItemLabel("changes", { ...QUIET, changedFiles: 2 }), "Changes, 2 changed files");
  assert.equal(railItemLabel("files", { ...QUIET, changedFiles: 2 }), "Files");
  assert.equal(railItemLabel("memory", QUIET), "Memory and learning");
});

const MODES = [
  // narrow window, unfolded by its button, a panel open, mode
  [false, false, false, "rail"],
  [false, true, true, "rail"],
  [true, false, false, "folded"],
  [true, true, false, "drawer"],
  [true, false, true, "drawer"],
  [true, true, true, "drawer"],
];

for (const [narrow, unfolded, panelOpen, mode] of MODES) {
  test(`narrow ${narrow}, unfolded ${unfolded}, panel open ${panelOpen}: ${mode}`, () => {
    assert.equal(railMode(narrow, unfolded, panelOpen), mode);
  });
}
