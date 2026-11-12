// Tests of the line that says which Blueprint version a replay built (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { blueprintLine } from "./replayBlueprint.ts";

const COMMIT = "3f2a1bc9d0e4a5b6c7d8e9f00112233445566778";

test("each layer names its commit, changes and source", () => {
  const layers = [
    { kind: "global", commit: COMMIT, dirty: false, source: "original", ref: null },
    { kind: "project", commit: COMMIT, dirty: true, source: "original", ref: null },
    { kind: "environment", commit: null, dirty: false, source: "current", ref: null },
  ];
  assert.equal(
    blueprintLine(layers),
    "Blueprint: global at 3f2a1bc, project at 3f2a1bc (had uncommitted changes then), environment as it is now",
  );
});

test("a variant's layer names its ref", () => {
  const layers = [{ kind: "project", commit: null, dirty: false, source: "variant", ref: "feature/x" }];
  assert.equal(blueprintLine(layers), "Blueprint: project at feature/x (variant)");
});

test("a replay stored without layers has no line", () => {
  assert.equal(blueprintLine([]), "");
});
