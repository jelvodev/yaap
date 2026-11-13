// Tests of the Waiting for me page's grouping and row targets (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { donePercent, groupWaiting, SECTIONS, waitingTarget } from "./waitingLogic.ts";

function row(kind, extra = {}) {
  return { kind, project_id: 1, card_id: 2, conversation_id: 3, ...extra };
}

test("rows are grouped in the sections' order, keeping the order of the rows inside a section", () => {
  const rows = [
    row("new_inbox", { card_id: 1 }),
    row("approve_plan", { card_id: 2 }),
    row("approve_action", { card_id: null }),
    row("approve_plan", { card_id: 3 }),
    row("continue_build", { card_id: null }),
    row("judge_review", { card_id: 4 }),
    row("answer_question", { card_id: 5 }),
  ];
  const groups = groupWaiting(rows);
  assert.deepEqual(
    groups.map((group) => group.kind),
    ["approve_action", "approve_plan", "judge_review", "answer_question", "new_inbox", "continue_build"],
  );
  assert.deepEqual(
    groups.find((group) => group.kind === "approve_plan").items.map((item) => item.card_id),
    [2, 3],
  );
  assert.equal(groups[0].title, SECTIONS[0].title);
});

test("empty sections and unknown kinds are left out", () => {
  assert.deepEqual(groupWaiting([]), []);
  const groups = groupWaiting([row("something_new"), row("judge_review")]);
  assert.deepEqual(
    groups.map((group) => group.kind),
    ["judge_review"],
  );
});

test("a row leads to its card, else its conversation, else its project's board", () => {
  assert.deepEqual(waitingTarget(row("approve_plan")), { kind: "card", projectId: 1, cardId: 2 });
  assert.deepEqual(waitingTarget(row("approve_action", { card_id: null })), { kind: "conversation", conversationId: 3 });
  assert.deepEqual(waitingTarget(row("continue_build", { card_id: null, conversation_id: null })), {
    kind: "board",
    projectId: 1,
  });
  assert.deepEqual(waitingTarget(row("approve_action", { project_id: null, card_id: null })), {
    kind: "conversation",
    conversationId: 3,
  });
  assert.equal(waitingTarget(row("approve_action", { project_id: null, card_id: null, conversation_id: null })), null);
});

test("the done share is a rounded percentage, 0 without cards and never over 100", () => {
  assert.equal(donePercent(0, 0), 0);
  assert.equal(donePercent(1, 3), 33);
  assert.equal(donePercent(3, 3), 100);
  assert.equal(donePercent(9, 3), 100);
});
