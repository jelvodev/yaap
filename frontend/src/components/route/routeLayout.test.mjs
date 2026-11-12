// Tests of the route layout (npm run test:unit). Node strips the types of routeLayout.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { formatClock, layoutRoute, stopLabel } from "./routeLayout.ts";

const STAGES = { tools: "tool", worker: "worker", afterwards: "afterwards", main: "model" };

function stop(id, lane, overrides = {}) {
  return { id, stage: STAGES[lane] ?? "note", lane, label: id, count: 1, state: "ran", checks: [], items: [], ...overrides };
}

const MAIN = [stop("message", "main"), stop("model:1:1", "main"), stop("model:1:3", "main"), stop("answer", "main")];
const at = (layout, id) => layout.stops.find((entry) => entry.stop.id === id);

test("stacked calls of one tool get a count in their label", () => {
  assert.equal(stopLabel({ label: "read_file", count: 3 }), "read_file ×3");
  assert.equal(stopLabel({ label: "edit_file", count: 1 }), "edit_file");
  const layout = layoutRoute([...MAIN, stop("tool:1:2", "tools", { label: "read_file", count: 3, parent: "model:1:1" })]);
  assert.equal(at(layout, "tool:1:2").label, "read_file ×3");
});

test("a tool branch hangs below its parent and rejoins at the next main-line stop", () => {
  const layout = layoutRoute([...MAIN, stop("tool:1:2", "tools", { parent: "model:1:1" })]);
  const tool = at(layout, "tool:1:2");
  assert.equal(tool.row, 1);
  assert.ok(tool.col > 1 && tool.col < 2);
  const branch = layout.segments.find((segment) => segment.lane === "tools");
  assert.deepEqual(branch.points[0], [1, 0]);
  assert.deepEqual(branch.points.at(-1), [2, 0]);
});

test("a worker line runs above the main line and rejoins", () => {
  const layout = layoutRoute([...MAIN, stop("worker:2", "worker", { line: "run-2", parent: "model:1:1" })]);
  assert.equal(at(layout, "worker:2").row, -1);
  assert.equal(layout.minRow, -1);
  const line = layout.segments.find((segment) => segment.lane === "worker");
  assert.deepEqual(line.points.at(-1), [2, 0]);
});

test("stops after the answer hang below the tools, dashed and to the right of the answer", () => {
  const layout = layoutRoute([
    ...MAIN,
    stop("tool:1:2", "tools", { parent: "model:1:1" }),
    stop("after:title", "afterwards", { parent: "answer" }),
    stop("after:summary", "afterwards", { parent: "answer" }),
  ]);
  const title = at(layout, "after:title");
  assert.equal(title.row, 2);
  assert.ok(title.col > at(layout, "answer").col);
  assert.ok(at(layout, "after:summary").col > title.col);
  assert.equal(layout.segments.find((segment) => segment.lane === "afterwards").dashed, true);
});

test("a stage or lane it does not know is a plain stop on the main line", () => {
  const layout = layoutRoute([
    ...MAIN.slice(0, 2),
    stop("note:1:5", "main", { stage: "escalation" }),
    stop("odd", "sideways", { stage: "note", label: "Odd" }),
  ]);
  const odd = at(layout, "odd");
  assert.equal(odd.row, 0);
  assert.equal(odd.lane, "main");
  assert.equal(odd.generic, true);
  assert.equal(at(layout, "note:1:5").generic, true);
  assert.equal(at(layout, "message").generic, false);
});

test("an empty route and a branch without a known parent still lay out", () => {
  assert.equal(layoutRoute([]).stops.length, 0);
  const layout = layoutRoute([...MAIN, stop("tool:x", "tools", { parent: "gone" })]);
  assert.equal(at(layout, "tool:x").row, 1);
});

test("a duration reads as a clock", () => {
  assert.equal(formatClock(68_000), "1:08");
  assert.equal(formatClock(400), "0:00");
});
