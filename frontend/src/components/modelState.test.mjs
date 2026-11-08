// Tests of what a model list says about a model (npm run test:unit). Node strips the types of modelState.ts as it
// loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { modelState, stateMark, stateSuffix } from "./modelState.ts";

const POLICY = "Refused by the model policy of the environment Work: only local models.";
const NO_KEY = "Add an Anthropic API key in Settings to use this model.";

const CASES = [
  // marks, kind, reason, mark
  [{ available: true, allowed: true, unavailable_reason: null }, "ok", undefined, ""],
  [{ available: true }, "ok", undefined, ""],
  [{ available: false, allowed: true, unavailable_reason: NO_KEY }, "no_key", NO_KEY, "no key"],
  [{ available: false, unavailable_reason: NO_KEY }, "no_key", NO_KEY, "no key"],
  [{ available: false, allowed: false, unavailable_reason: POLICY }, "not_allowed", POLICY, "not allowed"],
  // The policy wins over a missing key: a key would not make the model usable.
  [{ available: false, allowed: false }, "not_allowed", null, "not allowed"],
];

for (const [marks, kind, reason, mark] of CASES) {
  test(`a model marked ${JSON.stringify(marks)} is ${kind}`, () => {
    const state = modelState(marks);
    assert.equal(state.kind, kind);
    assert.equal(state.reason, reason);
    assert.equal(stateMark(state), mark);
    assert.equal(stateSuffix(state), mark ? ` (${mark})` : "");
  });
}
