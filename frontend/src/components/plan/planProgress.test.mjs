// Tests of the plan card's pure helpers (npm run test:unit). Node strips the types of planProgress.ts.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  attemptsLabel,
  checkLine,
  isFolded,
  moved,
  nonEmptyLines,
  outsidePlanSentence,
  parseCheckLine,
  planProgress,
  progressLine,
  shortCommit,
  statusLabel,
  uncommittedSentence,
} from "./planProgress.ts";

test("planProgress counts done and skipped steps", () => {
  const rows = [
    [[], { done: 0, total: 0 }],
    [["open", "open"], { done: 0, total: 2 }],
    [["done", "working", "open"], { done: 1, total: 3 }],
    [["done", "skipped", "failed"], { done: 2, total: 3 }],
  ];
  for (const [statuses, expected] of rows) {
    assert.deepEqual(planProgress(statuses.map((status) => ({ status }))), expected);
  }
});

test("statusLabel names every status and only a cancelled plan is folded", () => {
  const labels = {
    draft: "Draft",
    approved: "Approved",
    queued: "Queued",
    building: "Building",
    interrupted: "Interrupted",
    done: "Done",
    failed: "Failed",
    cancelled: "Discarded",
  };
  for (const [status, label] of Object.entries(labels)) {
    assert.equal(statusLabel(status), label);
    assert.equal(isFolded(status), status === "cancelled");
  }
});

test("checkLine and parseCheckLine round-trip a check with and without a command", () => {
  assert.equal(checkLine({ text: "It answers", run: "pytest -q" }), "It answers run: pytest -q");
  assert.equal(checkLine({ text: "A person looks", run: null }), "A person looks");
  assert.deepEqual(parseCheckLine("It answers run: pytest -q"), { text: "It answers", run: "pytest -q" });
  assert.deepEqual(parseCheckLine("  A person looks "), { text: "A person looks", run: null });
});

test("parseCheckLine takes everything after the first run: as the command", () => {
  assert.deepEqual(parseCheckLine("x run: echo run: hi"), { text: "x", run: "echo run: hi" });
  assert.deepEqual(parseCheckLine("run: pytest"), { text: "pytest", run: "pytest" });
  assert.deepEqual(parseCheckLine("only text run:"), { text: "only text", run: null });
});

test("nonEmptyLines trims lines and drops empty ones", () => {
  assert.deepEqual(nonEmptyLines(" a \n\n  \n b\n"), ["a", "b"]);
  assert.deepEqual(nonEmptyLines(""), []);
});

test("moved shifts an item and leaves the list alone at the ends", () => {
  const rows = [
    [[0, 1], ["b", "a", "c"]],
    [[1, -1], ["b", "a", "c"]],
    [[0, -1], ["a", "b", "c"]],
    [[2, 1], ["a", "b", "c"]],
    [[5, 1], ["a", "b", "c"]],
  ];
  for (const [[index, offset], expected] of rows) {
    assert.deepEqual(moved(["a", "b", "c"], index, offset), expected);
  }
  const original = ["a", "b"];
  assert.notEqual(moved(original, 0, 5), original);
});

test("attemptsLabel groups consecutive equal roles", () => {
  const rows = [
    [[], ""],
    [["worker"], "worker"],
    [["worker", "worker"], "worker ×2"],
    [["worker", "worker", "main"], "worker ×2 → main"],
    [["worker", "main", "worker"], "worker → main → worker"],
  ];
  for (const [roles, expected] of rows) {
    assert.equal(attemptsLabel(roles), expected);
  }
});

test("uncommittedSentence is empty for none and singular for one", () => {
  assert.equal(uncommittedSentence(0), "");
  assert.equal(uncommittedSentence(-1), "");
  assert.equal(uncommittedSentence(1), "1 uncommitted change is not included.");
  assert.equal(uncommittedSentence(3), "3 uncommitted changes are not included.");
});

test("progressLine shows the cost with two decimals and leaves it out when unknown", () => {
  assert.equal(progressLine(3, 5, 0.84), "3 of 5 steps · $0.84");
  assert.equal(progressLine(0, 2, 0), "0 of 2 steps · $0.00");
  assert.equal(progressLine(1, 1, null), "1 of 1 steps");
});

test("shortCommit is the first seven characters", () => {
  assert.equal(shortCommit("0123456789abcdef"), "0123456");
  assert.equal(shortCommit("abc"), "abc");
});

test("parseCheckLine reads a check written as a code span without its backticks", () => {
  assert.deepEqual(parseCheckLine("It answers `run: pytest -q`"), { text: "It answers", run: "pytest -q" });
  assert.deepEqual(parseCheckLine("`run: pytest -q`"), { text: "pytest -q", run: "pytest -q" });
});

test("outsidePlanSentence names the step, the paths and how many more there were", () => {
  const rows = [
    [
      { step: 2, paths: ["src/storage.py", "src/inventory.py"], total: 2 },
      "Step 2 changed files outside the plan: src/storage.py, src/inventory.py. Other builds wait for this one as if it touched the whole project.",
    ],
    [
      { step: 1, paths: ["a.py"], total: 4 },
      "Step 1 changed files outside the plan: a.py and 3 more. Other builds wait for this one as if it touched the whole project.",
    ],
  ];
  for (const [item, expected] of rows) {
    assert.equal(outsidePlanSentence(item), expected);
  }
});
