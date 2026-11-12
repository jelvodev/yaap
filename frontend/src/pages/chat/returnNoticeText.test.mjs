// Tests of the words of the notice after a pause (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { duration, formatCost, formatThousands, returnNoticeText } from "./returnNoticeText.ts";

const COSTS = [
  [0.5, "$0.50"],
  [0.0125, "$0.01"],
  [0.004, "< $0.01"],
  [12, "$12.00"],
];

for (const [dollars, text] of COSTS) {
  test(`${dollars} dollars is written ${text}`, () => {
    assert.equal(formatCost(dollars), text);
  });
}

test("token counts are written in thousands", () => {
  assert.equal(formatThousands(100_000), "100k");
  assert.equal(formatThousands(850), "850");
});

test("a metered notice names the cost and a subscription notice only the tokens", () => {
  const base = { cache_lifetime_minutes: 5, context_tokens: 100_000 };
  assert.match(returnNoticeText({ ...base, billing: "metered", cost: 0.5 }), /writes its 100k tokens.*\$0\.50/);
  const subscription = returnNoticeText({ ...base, billing: "subscription", cost: null });
  assert.match(subscription, /reads about 100k tokens again/);
  assert.doesNotMatch(subscription, /\$/);
});

const DURATIONS = [
  [5, "5 minutes"],
  [60, "an hour"],
  [120, "2 hours"],
];

for (const [minutes, text] of DURATIONS) {
  test(`a cache lifetime of ${minutes} minutes is written ${text}`, () => {
    assert.equal(duration(minutes), text);
  });
}
