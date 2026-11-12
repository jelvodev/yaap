// Tests of the built-in layer's file text and cost label (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { builtinFileText, formatCost, itemsOfKind } from "./builtinLogic.ts";

const FILE_TEXTS = [
  // description, instructions, expected file
  ["Plain words", "Body\n", "---\ndescription: Plain words\n---\n\nBody\n"],
  ["Has: a colon", "Body", '---\ndescription: "Has: a colon"\n---\n\nBody\n'],
  ["Two\nlines", "\nBody\n\n", "---\ndescription: Two lines\n---\n\nBody\n"],
  ['Says "hi"', "B", '---\ndescription: "Says \\"hi\\""\n---\n\nB\n'],
];
for (const [description, instructions, expected] of FILE_TEXTS) {
  test(`builtin file text for description ${JSON.stringify(description)}`, () => {
    assert.equal(builtinFileText({ description, instructions }), expected);
  });
}

const COSTS = [
  [0, "$0.00"],
  [0.004, "< $0.01"],
  [0.5, "$0.50"],
  [12.345, "$12.35"],
];
for (const [cost, expected] of COSTS) {
  test(`cost ${cost} is shown as ${expected}`, () => {
    assert.equal(formatCost(cost), expected);
  });
}

test("items are filtered by kind in order", () => {
  const items = [
    { kind: "skill", name: "a" },
    { kind: "agent", name: "b" },
    { kind: "skill", name: "c" },
  ];
  assert.deepEqual(
    itemsOfKind(items, "skill").map((item) => item.name),
    ["a", "c"],
  );
});
