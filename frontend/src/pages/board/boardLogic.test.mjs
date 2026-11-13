// Tests of the board page's pure logic (npm run test:unit): grouping, the version filter, progress, the budget colour,
// the drop table, the tile's state and the buttons of each column.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  budgetColour,
  canDrop,
  cardState,
  columnButtons,
  COLUMNS,
  filterByVersion,
  filterKey,
  groupByColumn,
  nextColumn,
  parseFilterKey,
  versionProgress,
} from "./boardLogic.ts";

function card(id, column, extra = {}) {
  return { id, column, version: null, broken: false, state: "idle", plan: null, review: "none", ...extra };
}

const ids = (cards) => cards.map((item) => item.id);

test("cards are grouped per column in board order and a broken card is left out", () => {
  const cards = [card(1, "design"), card(2, "inbox"), card(3, "design"), card(4, null, { broken: true })];
  const groups = groupByColumn(cards);
  assert.deepEqual(Object.keys(groups), [...COLUMNS]);
  assert.deepEqual(ids(groups.design), [1, 3]);
  assert.deepEqual(ids(groups.inbox), [2]);
  assert.deepEqual(ids(groups.done), []);
  assert.equal(Object.values(groups).flat().length, 3);
});

test("the version filter shows one version, the unscheduled cards or all, and Inbox under every filter", () => {
  const cards = [
    card(1, "idea", { version: "0.8.0" }),
    card(2, "idea", { version: "0.9.0" }),
    card(3, "idea"),
    card(4, "inbox", { version: "0.9.0" }),
    card(5, "inbox"),
  ];
  assert.deepEqual(ids(filterByVersion(cards, { kind: "all" })), [1, 2, 3, 4, 5]);
  assert.deepEqual(ids(filterByVersion(cards, { kind: "version", name: "0.8.0" })), [1, 4, 5]);
  assert.deepEqual(ids(filterByVersion(cards, { kind: "unscheduled" })), [3, 4, 5]);
  assert.deepEqual(ids(filterByVersion(cards, { kind: "version", name: "nope" })), [4, 5]);
});

test("a filter is kept as text and read back, and anything unreadable is All", () => {
  for (const filter of [{ kind: "all" }, { kind: "unscheduled" }, { kind: "version", name: "0.8.0" }]) {
    assert.deepEqual(parseFilterKey(filterKey(filter)), filter);
  }
  for (const value of [null, "", "version:", "something", "Unscheduled"]) {
    assert.deepEqual(parseFilterKey(value), { kind: "all" });
  }
});

test("progress is the rounded share of done cards, 0 without cards and never over 100", () => {
  assert.deepEqual(versionProgress(0, 0), { done: 0, total: 0, percent: 0 });
  assert.equal(versionProgress(1, 3).percent, 33);
  assert.equal(versionProgress(2, 3).percent, 67);
  assert.equal(versionProgress(3, 3).percent, 100);
  assert.equal(versionProgress(5, 3).percent, 100);
});

test("the budget colour is none without a budget, warn from 80 % and over above 100 %", () => {
  const cases = [
    [79, 100, "ok"],
    [80, 100, "warn"],
    [100, 100, "warn"],
    [101, 100, "over"],
    [0, 100, "ok"],
    [5, null, "none"],
    [0, 0, "ok"],
    [1, 0, "over"],
  ];
  for (const [spent, budget, expected] of cases) {
    assert.equal(budgetColour(spent, budget), expected, `${spent} of ${budget}`);
  }
});

test("a card may be dropped only where the flow lets a move go", () => {
  const plan = (status) => ({ status });
  const cases = [
    // [column, extra, target, ok]
    ["inbox", {}, "idea", true],
    ["inbox", {}, "design", true],
    ["inbox", {}, "plan", false],
    ["idea", {}, "design", true],
    ["idea", {}, "plan", false],
    ["design", {}, "plan", true],
    ["design", {}, "idea", true],
    ["design", {}, "build", false],
    ["plan", { plan: plan("draft") }, "build", false],
    ["plan", { plan: plan("draft") }, "design", true],
    ["plan", { plan: plan("building") }, "design", false],
    ["build", { plan: plan("building") }, "plan", false],
    ["build", { plan: plan("failed") }, "plan", false],
    ["build", { plan: plan("failed") }, "design", true],
    ["build", { plan: plan("done") }, "review", true],
    ["build", { plan: plan("failed") }, "review", false],
    ["build", { plan: plan("done") }, "done", false],
    ["review", { review: "done" }, "done", true],
    ["review", { review: "running" }, "done", false],
    ["review", { review: "none" }, "done", false],
    ["review", {}, "build", true],
    ["done", {}, "review", true],
    ["done", {}, "done", false],
    ["idea", { broken: true }, "design", false],
  ];
  for (const [column, extra, target, ok] of cases) {
    assert.equal(canDrop(card(1, column, extra), target).ok, ok, `${column} to ${target} ${JSON.stringify(extra)}`);
  }
  assert.equal(canDrop(card(1, null, { broken: true }), "idea").ok, false);
  const refused = canDrop(card(1, "plan", { plan: plan("draft") }), "build");
  assert.equal(refused.ok, false);
  assert.match(refused.reason, /Approve and build/);
});

test("the next column is offered only when the flow allows the move", () => {
  assert.equal(nextColumn(card(1, "idea")), "design");
  assert.equal(nextColumn(card(1, "plan", { plan: { status: "draft" } })), null);
  assert.equal(nextColumn(card(1, "build", { plan: { status: "done" } })), "review");
  assert.equal(nextColumn(card(1, "done")), null);
  assert.equal(nextColumn(card(1, null, { broken: true })), null);
});

test("a tile's state reads with its tone and a short line, and idle has no line", () => {
  assert.deepEqual(cardState({ state: "broken" }), { tone: "broken", label: "Cannot be read" });
  assert.deepEqual(cardState({ state: "working" }), { tone: "working", label: "Working" });
  assert.deepEqual(cardState({ state: "failed" }), { tone: "failed", label: "Build failed" });
  assert.deepEqual(cardState({ state: "interrupted" }), { tone: "interrupted", label: "Interrupted" });
  assert.deepEqual(cardState({ state: "waiting" }), { tone: "waiting", label: "Waiting for you" });
  assert.deepEqual(cardState({ state: "idle" }), { tone: "idle", label: "" });
});

test("each column shows the buttons its state allows, the due step first", () => {
  const facts = (column, extra = {}) => ({
    column,
    broken: false,
    planStatus: null,
    planMissing: false,
    review: "none",
    blockedBy: [],
    ...extra,
  });
  const cases = [
    [facts("inbox"), []],
    [facts("idea"), ["specify"]],
    [facts("design"), ["plan", "move_to_idea"]],
    [facts("plan"), ["plan"]],
    [facts("plan", { planStatus: "cancelled" }), ["plan"]],
    [facts("plan", { planStatus: "draft", planMissing: true }), ["plan"]],
    [facts("plan", { planStatus: "draft" }), ["build", "edit_plan", "ask_change"]],
    [facts("plan", { planStatus: "draft", blockedBy: [3] }), ["edit_plan", "ask_change"]],
    [facts("plan", { planStatus: "building" }), []],
    [facts("build", { planStatus: "building" }), ["stop"]],
    [facts("build", { planStatus: "queued" }), ["stop"]],
    [facts("build", { planStatus: "failed" }), ["continue", "send_back"]],
    [facts("build", { planStatus: "interrupted" }), ["continue", "send_back"]],
    [facts("build", { planStatus: "done" }), ["send_back"]],
    [facts("build", { planStatus: "cancelled" }), ["send_back"]],
    [facts("build", { planStatus: "approved" }), []],
    [facts("review"), ["review", "mark_done"]],
    [facts("review", { review: "done" }), ["review", "mark_done"]],
    [facts("review", { review: "running" }), ["mark_done"]],
    [facts("done"), []],
    [facts("idea", { broken: true }), []],
    [facts(null, { broken: true }), []],
  ];
  for (const [input, expected] of cases) {
    assert.deepEqual(columnButtons(input), expected, JSON.stringify(input));
  }
});
