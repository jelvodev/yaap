// Tests of the sign-in state helpers (npm run test:unit). Node strips the types of signInState.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { isRunning, justSignedIn, openingState } from "./signInState.ts";

const RUNNING = [
  // state, running
  ["idle", false],
  ["starting", true],
  ["waiting_for_code", true],
  ["waiting_for_browser", true],
  ["signed_in", false],
  ["failed", false],
  ["cancelled", false],
];

for (const [state, running] of RUNNING) {
  test(`a sign-in in the state ${state} ${running ? "is" : "is not"} running`, () => {
    assert.equal(isRunning(state), running);
  });
}

test("only a running sign-in is picked up when the page opens", () => {
  const running = { state: "waiting_for_code", url: "https://claude.com/x" };
  assert.equal(openingState(running), running);
  assert.equal(openingState({ state: "failed" }), null);
  assert.equal(openingState({ state: "signed_in" }), null);
  assert.equal(openingState(null), null);
});

const SIGNED_IN = [
  // before, after, the moment of success
  ["waiting_for_code", "signed_in", true],
  ["starting", "signed_in", true],
  ["signed_in", "signed_in", false],
  [undefined, "signed_in", false],
  ["waiting_for_code", "failed", false],
];

for (const [before, after, moment] of SIGNED_IN) {
  test(`going from ${before} to ${after} ${moment ? "is" : "is not"} the moment of success`, () => {
    assert.equal(justSignedIn(before, after), moment);
  });
}
