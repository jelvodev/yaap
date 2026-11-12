// Tests of the context bar's widths (npm run test:unit). Node strips the types of contextBar.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { barSegments } from "./contextBar.ts";

const sum = (segments) => segments.reduce((total, segment) => total + segment.percent, 0);
const percents = (segments) => segments.map((segment) => segment.percent);

test("widths are relative to the limit when it is known", () => {
  const segments = barSegments(
    [
      { key: "a", group: "system", tokens: 250 },
      { key: "b", group: "messages", tokens: 250 },
    ],
    1000,
  );
  assert.deepEqual(percents(segments), [25, 25]);
});

test("without a limit the segments fill the bar", () => {
  const segments = barSegments(
    [
      { key: "a", group: "system", tokens: 100 },
      { key: "b", group: "tools", tokens: 300 },
    ],
    null,
  );
  assert.deepEqual(percents(segments), [25, 75]);
});

test("more than the limit still adds up to at most 100", () => {
  const segments = barSegments(
    [
      { key: "a", group: "system", tokens: 900 },
      { key: "b", group: "messages", tokens: 600 },
    ],
    1000,
  );
  assert.ok(sum(segments) <= 100 + 1e-9);
});

test("negative and empty sizes never give a negative width", () => {
  const segments = barSegments(
    [
      { key: "a", group: "system", tokens: -5 },
      { key: "b", group: "messages", tokens: 0 },
    ],
    null,
  );
  assert.deepEqual(percents(segments), [0, 0]);
});
