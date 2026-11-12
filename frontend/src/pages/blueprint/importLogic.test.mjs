// Tests of the import review's layer and duplicate logic (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { duplicatePath, effectiveLayer, replacesExisting, validName } from "./importLogic.ts";

const pathOf = (kind, name) => `${kind}/${name}`;
const choice = (kind, name, layer, include = true) => ({ include, kind, name, layer, fileOn: [] });

test("a panel at the global level has one layer", () => {
  assert.equal(effectiveLayer("global", true), "level");
  assert.equal(effectiveLayer("global", false), "global");
  assert.equal(effectiveLayer("level", false), "level");
});

const REPLACES = [
  // layer, exists in level, exists in global, replaces
  ["level", true, false, true],
  ["level", false, true, false],
  ["global", false, true, true],
  ["global", true, false, false],
];
for (const [layer, inLevel, inGlobal, expected] of REPLACES) {
  test(`replaces an existing item: ${layer} level=${inLevel} global=${inGlobal}`, () => {
    assert.equal(replacesExisting({ exists_in_level: inLevel, exists_in_global: inGlobal }, layer), expected);
  });
}

const DUPLICATES = [
  // choices, level is global, the duplicate path
  [[choice("skill", "a", "level"), choice("skill", "a", "level")], false, "skill/a"],
  [[choice("skill", "a", "level"), choice("skill", "a", "global")], false, null],
  [[choice("skill", "a", "level"), choice("skill", "a", "global")], true, "skill/a"],
  [[choice("skill", "a", "level"), choice("skill", "a", "level", false)], false, null],
];
for (const [choices, isGlobal, expected] of DUPLICATES) {
  test(`duplicate path per layer: ${JSON.stringify(expected)} (global panel ${isGlobal})`, () => {
    assert.equal(duplicatePath(choices, isGlobal, pathOf), expected);
  });
}

test("names are lowercase words except for the instructions", () => {
  assert.equal(validName({ kind: "instructions", name: "" }), true);
  assert.equal(validName({ kind: "skill", name: "Bad Name" }), false);
  assert.equal(validName({ kind: "skill", name: "good-name" }), true);
});
