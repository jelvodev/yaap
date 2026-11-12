// Tests of the token bars' math (npm run test:unit). Node strips the types of tokenBars.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { tokenSegments, largestTotal, percent, rowTotal } from "./tokenBars.ts";

function row(cacheRead, cacheWrite, input, output) {
  return {
    kind: "reply",
    label: "r",
    message_id: 1,
    run_id: 1,
    started_at: null,
    cache_read_tokens: cacheRead,
    cache_write_tokens: cacheWrite,
    input_tokens: input,
    output_tokens: output,
    cost: null,
  };
}

test("a row's total adds the four kinds", () => {
  assert.equal(rowTotal(row(1, 2, 3, 4)), 10);
});

test("the largest row fills the bar and a smaller one is scaled to it", () => {
  const rows = [row(50, 0, 25, 25), row(10, 0, 5, 5)];
  const max = largestTotal(rows);
  assert.equal(max, 100);
  assert.deepEqual(
    tokenSegments(rows[1], max).map((part) => part.width),
    [10, 0, 5, 5],
  );
  assert.equal(tokenSegments(rows[0], max).reduce((sum, part) => sum + part.width, 0), 100);
});

test("a negative count counts as zero", () => {
  assert.equal(rowTotal(row(-5, 0, 3, 4)), 7);
});

test("without any tokens every width is zero", () => {
  assert.equal(largestTotal([]), 0);
  assert.ok(tokenSegments(row(0, 0, 0, 0), 0).every((part) => part.width === 0));
});

test("the cache hit rate shows as a rounded percentage", () => {
  assert.equal(percent(0.876), "88%");
  assert.equal(percent(null), null);
});
