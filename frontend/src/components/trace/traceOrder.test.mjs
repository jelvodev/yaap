// Tests of the trace's row order (npm run test:unit). Node strips the types of traceOrder.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { barOf, formatCost, formatDuration, stepForToolCall, timeSpan, timelineRows } from "./traceOrder.ts";

const START = Date.parse("2026-11-08T09:00:00Z");

function at(seconds) {
  return new Date(START + seconds * 1000).toISOString();
}

let nextStep = 1;

function step(seconds, kind, tool = null, duration = 1000) {
  return { id: nextStep++, position: 0, kind, tool, started_at: at(seconds), duration_ms: duration, cost: null };
}

function run(id, seconds, steps, children = [], task = null) {
  return { id, task, started_at: at(seconds), finished_at: null, steps, children };
}

function labels(rows) {
  return rows.map((row) => "  ".repeat(row.depth) + (row.kind === "run" ? `run ${row.run.id}` : (row.step.tool ?? "model")));
}

test("a run of three levels shows each sub-run right after the call that started it", () => {
  const grandchild = run(3, 22, [step(22, "model"), step(23, "tool", "read_file")]);
  const child = run(2, 11, [step(11, "model"), step(20, "tool", "delegate", 8000), step(30, "model")], [grandchild]);
  const root = run(1, 0, [step(0, "model"), step(10, "tool", "delegate", 25000), step(40, "model")], [child]);

  assert.deepEqual(labels(timelineRows(root)), [
    "run 1",
    "model",
    "delegate",
    "  run 2",
    "  model",
    "  delegate",
    "    run 3",
    "    model",
    "    read_file",
    "  model",
    "model",
  ]);
});

test("sub-runs started in the same call keep the order they started in, also when listed out of order", () => {
  const late = run(5, 15, [step(15, "model")]);
  const early = run(4, 12, [step(12, "model")]);
  const root = run(1, 0, [step(0, "model"), step(10, "tool", "delegate", 10000), step(30, "model")], [late, early]);

  assert.deepEqual(labels(timelineRows(root)), [
    "run 1",
    "model",
    "delegate",
    "  run 4",
    "  model",
    "  run 5",
    "  model",
    "model",
  ]);
});

test("a sub-run that started after the last step comes at the end, and every row has its own key", () => {
  const child = run(2, 50, [step(50, "model")]);
  const root = run(1, 0, [step(0, "model")], [child]);
  const rows = timelineRows(root);

  assert.deepEqual(labels(rows), ["run 1", "model", "  run 2", "  model"]);
  assert.equal(new Set(rows.map((row) => row.key)).size, rows.length);
});

test("a step's bar sits within the trace's span", () => {
  const first = step(0, "model", null, 2000);
  const second = step(8, "tool", "list_files", 2000);
  const span = timeSpan(run(1, 0, [first, second]));

  assert.deepEqual(span, { start: START, end: START + 10000 });
  assert.deepEqual(barOf(first, span), { left: 0, width: 20 });
  assert.deepEqual(barOf(second, span), { left: 80, width: 20 });
});

test("durations and costs read as people write them", () => {
  assert.equal(formatDuration(450), "450 ms");
  assert.equal(formatDuration(2500), "2.5 s");
  assert.equal(formatDuration(125000), "2m 5s");
  assert.equal(formatCost(0.0042), "$0.0042");
  assert.equal(formatCost(1.5), "$1.50");
  assert.equal(formatCost(null), "");
});

test("a tool call of a reply finds its step by tool and place among that tool's calls", () => {
  const first = { ...step(1, "tool", "write_file"), position: 2 };
  const read = { ...step(2, "tool", "read_file"), position: 3 };
  const second = { ...step(3, "tool", "write_file"), position: 4 };
  const reply = run(1, 0, [{ ...step(0, "model"), position: 1 }, second, read, first]);
  assert.equal(stepForToolCall(reply, { name: "write_file", occurrence: 0 }), first);
  assert.equal(stepForToolCall(reply, { name: "write_file", occurrence: 1 }), second);
  assert.equal(stepForToolCall(reply, { name: "read_file", occurrence: 0 }), read);
  assert.equal(stepForToolCall(reply, { name: "write_file", occurrence: 2 }), null);
  assert.equal(stepForToolCall(reply, { name: "run_command", occurrence: 0 }), null);
});
