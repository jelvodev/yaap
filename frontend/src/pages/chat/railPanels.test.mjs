// Tests of the project rail's panels and the links that open them (npm run test:unit). Node strips the types of
// railPanels.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PANEL_TABS,
  RAIL_PANELS,
  RAIL_VIEWS,
  linkFromLocation,
  locationFromLink,
  panelTabOf,
  railLocation,
} from "./railPanels.ts";

const OLD_TABS = [
  // old tab value, the panel it opens, the view inside it
  ["changes", "changes", null],
  ["files", "files", null],
  ["branches", "branches", null],
  ["history", "history", "history"],
  ["checkpoints", "history", "checkpoints"],
  ["memory", "memory", "memory"],
  ["knowledge", "knowledge", null],
  ["tasks", "tasks", null],
  ["evals", "evals", "evals"],
  ["learning", "memory", "learning"],
  ["reviews", "evals", "reviews"],
  ["project", "settings", null],
];

test("the table covers every old tab value", () => {
  assert.deepEqual(OLD_TABS.map(([tab]) => tab).sort(), [...PANEL_TABS].sort());
});

for (const [tab, panel, view] of OLD_TABS) {
  test(`the old tab ${tab} opens ${panel}${view ? ` on ${view}` : ""}`, () => {
    assert.deepEqual(locationFromLink(tab), { panel, view });
  });

  test(`the location of ${tab} links back to ${tab}`, () => {
    assert.equal(linkFromLocation(locationFromLink(tab)), tab);
  });
}

test("every location links to a value that opens the same location", () => {
  for (const panel of RAIL_PANELS) {
    const views = RAIL_VIEWS[panel] ?? [null];
    for (const view of views) {
      const location = railLocation(panel, view);
      assert.deepEqual(locationFromLink(linkFromLocation(location)), location, `${panel} ${view}`);
    }
  }
});

test("the rail's own names open their panels too", () => {
  assert.deepEqual(locationFromLink("preview"), { panel: "preview", view: null });
  assert.deepEqual(locationFromLink("settings"), { panel: "settings", view: null });
  assert.equal(linkFromLocation({ panel: "preview", view: null }), "preview");
});

test("unknown or missing link values open nothing", () => {
  for (const value of [null, "", "Project", "checkpoint", "history?x=1", "toString"]) {
    assert.equal(locationFromLink(value), null, String(value));
  }
});

test("a panel opens on its first view, or on a view of its own", () => {
  assert.deepEqual(railLocation("history"), { panel: "history", view: "history" });
  assert.deepEqual(railLocation("memory", "learning"), { panel: "memory", view: "learning" });
  assert.deepEqual(railLocation("evals", "learning"), { panel: "evals", view: "evals" });
  assert.deepEqual(railLocation("files", "reviews"), { panel: "files", view: null });
});

test("the project panel's tab follows the location; the preview has none", () => {
  assert.equal(panelTabOf(null), null);
  assert.equal(panelTabOf({ panel: "preview", view: null }), null);
  assert.equal(panelTabOf({ panel: "settings", view: null }), "project");
  assert.equal(panelTabOf({ panel: "evals", view: "reviews" }), "reviews");
});
