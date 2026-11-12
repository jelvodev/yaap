// Tests of the second opinion's texts (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { composerText, estimateLine, foldedLine, pointsText } from "./secondOpinionText.ts";

const done = {
  status: "done",
  agrees: true,
  cost: 0.02,
  reason: null,
  summary: "Looks right.",
  alternative: "Use a map.",
  reviewer_model: "gpt-x",
  findings: [
    { severity: "high", path: "a.py", line: 3, explanation: "Off by one." },
    { severity: "low", path: "", line: null, explanation: "Naming." },
  ],
};

const FOLDED = [
  [done, "Second opinion · agrees · 2 points (1 high) · $0.02"],
  [{ ...done, agrees: false, findings: [], cost: null }, "Second opinion · disagrees · no points"],
  [{ status: "running" }, "Second opinion · running…"],
  [{ status: "skipped", reason: "no key" }, "Second opinion · skipped: no key"],
];

for (const [opinion, line] of FOLDED) {
  test(`a ${opinion.status} opinion folds to "${line}"`, () => {
    assert.equal(foldedLine(opinion), line);
  });
}

const ESTIMATES = [
  [{ input_tokens: 9000, output_tokens: 3000, usd: 0.034 }, "about $0.03"],
  [{ input_tokens: 9000, output_tokens: 3000, usd: null }, "subscription: about 12k tokens"],
  [null, "No estimate available."],
];

for (const [estimate, line] of ESTIMATES) {
  test(`an estimate reads "${line}"`, () => {
    assert.equal(estimateLine(estimate), line);
  });
}

test("a single point is not pluralised", () => {
  assert.equal(pointsText({ findings: [done.findings[1]] }), "1 point");
});

test("the composer text lists the points with their place and the alternative", () => {
  assert.equal(
    composerText(done),
    [
      "A second opinion (gpt-x) on your last reply:",
      "Looks right.",
      "- [high] a.py:3: Off by one.",
      "- [low] Naming.",
      "What it would do differently: Use a map.",
      "Please take this into account.",
    ].join("\n"),
  );
});
