// Tests of when the reload bar shows (npm run test:unit). Node strips the types of reloadTarget.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { reloadTarget } from "./reloadTarget.ts";

test("the reload bar names the running version when it differs from the build", () => {
  assert.equal(reloadTarget("0.5.7", "0.6.0"), "0.6.0");
});

test("the reload bar stays away when the versions match or one is unknown", () => {
  assert.equal(reloadTarget("0.5.7", "0.5.7"), null);
  assert.equal(reloadTarget("", "0.6.0"), null);
  assert.equal(reloadTarget("0.5.7", null), null);
  assert.equal(reloadTarget("0.5.7", undefined), null);
});
