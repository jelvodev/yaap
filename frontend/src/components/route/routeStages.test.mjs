// Tests of the streaming route's updates (npm run test:unit).

import assert from "node:assert/strict";
import { test } from "node:test";

import { removeStop, upsertStop } from "./routeStages.ts";

const stop = (id, state = "ran") => ({ id, state });

test("a stop with a known id replaces the earlier one in place, a new id is added at the end", () => {
  const stops = [stop("a"), stop("b", "waiting"), stop("c")];
  assert.deepEqual(upsertStop(stops, stop("b")).map((s) => [s.id, s.state]), [["a", "ran"], ["b", "ran"], ["c", "ran"]]);
  assert.deepEqual(upsertStop(stops, stop("d")).map((s) => s.id), ["a", "b", "c", "d"]);
});

test("a removed stop leaves the others in order; an unknown id changes nothing", () => {
  const stops = [stop("a"), stop("b"), stop("c")];
  assert.deepEqual(removeStop(stops, "b").map((s) => s.id), ["a", "c"]);
  assert.deepEqual(removeStop(stops, "x").map((s) => s.id), ["a", "b", "c"]);
});
