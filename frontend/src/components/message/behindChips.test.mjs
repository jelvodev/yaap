// Tests of the helpers behind the labels under a reply (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { callWhyFor, costText, labelLines, splitChips } from "./behindChips.ts";

const SPLITS = [
  // count, max, shown, more
  [0, 7, 0, 0],
  [7, 7, 7, 0],
  [9, 7, 7, 2],
  [3, 2, 2, 1],
];

for (const [count, max, shown, more] of SPLITS) {
  test(`${count} labels with room for ${max} show ${shown} and hide ${more}`, () => {
    const result = splitChips(Array.from({ length: count }, (_, index) => index), max);
    assert.equal(result.shown.length, shown);
    assert.equal(result.more.length, more);
  });
}

const COSTS = [
  [null, ""],
  [0, "free"],
  [0.004, "$0.0040"],
  [1.236, "$1.24"],
];

for (const [cost, text] of COSTS) {
  test(`a cost of ${cost} reads "${text}"`, () => {
    assert.equal(costText(cost), text);
  });
}

const LINES = [
  [{ cost: null, tokens: null }, []],
  [{ cost: 1.5, tokens: null }, ["Cost: $1.50"]],
  [{ cost: null, tokens: 1200 }, ["Tokens: 1,200"]],
  [{ cost: 0, tokens: 5 }, ["Cost: free", "Tokens: 5"]],
];

for (const [label, lines] of LINES) {
  test(`a label with cost ${label.cost} and tokens ${label.tokens} has ${lines.length} lines`, () => {
    assert.deepEqual(labelLines(label), lines);
  });
}

test("a call is found by its key and an unknown key finds nothing", () => {
  const calls = [{ call_key: "a" }, { call_key: "b" }, { call_key: null }];
  assert.equal(callWhyFor(calls, "b"), calls[1]);
  assert.equal(callWhyFor(calls, "z"), undefined);
});
