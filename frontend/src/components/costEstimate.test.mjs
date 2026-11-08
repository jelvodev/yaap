// Tests of the estimate line (npm run test:unit). Node strips the types of costEstimate.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { estimateText } from "./costEstimate.ts";

const NONE = { runs: 2, min_runs: 3, median_cost: null, high_cost: null, median_tokens: null, high_tokens: null };

test("too few earlier runs give no estimate and say how many are needed", () => {
  assert.equal(estimateText(NONE, "goals"), "No estimate yet: it needs 3 earlier goals (2 so far).");
});

test("an estimate shows the median and the high value in dollars", () => {
  const found = { runs: 7, min_runs: 3, median_cost: 0.424, high_cost: 1.3, median_tokens: 90_000, high_tokens: 400_000 };
  assert.equal(estimateText(found, "runs"), "Estimate from 7 earlier runs: usually $0.42, up to $1.30.");
});

test("runs that cost nothing per call are estimated in tokens", () => {
  const found = { runs: 3, min_runs: 3, median_cost: 0, high_cost: 0, median_tokens: 85_400, high_tokens: 1_250_000 };
  assert.equal(estimateText(found, "goals"), "Estimate from 3 earlier goals: usually 85k tokens, up to 1.3M tokens.");
});
