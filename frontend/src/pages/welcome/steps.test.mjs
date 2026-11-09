// Tests of the first start's steps (npm run test:unit). Node strips the types of steps.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { neighbour, resumeStep } from "./steps.ts";

function status(started, steps) {
  return { started, steps: { connection: "todo", project: "todo", backups: "todo", starter: "todo", ...steps } };
}

const RESUMES = [
  // started, step states, where the wizard opens
  [false, {}, "welcome"],
  [false, { connection: "done", project: "done" }, "welcome"],
  [true, {}, "connection"],
  [true, { connection: "done" }, "project"],
  [true, { connection: "skipped", project: "done" }, "backups"],
  [true, { connection: "done", project: "done", backups: "skipped" }, "starter"],
  [true, { connection: "done", project: "skipped", backups: "done", starter: "done" }, "done"],
  [true, { connection: "done", project: "done", backups: "done", starter: "todo" }, "starter"],
];

for (const [started, steps, expected] of RESUMES) {
  test(`opens at ${expected} when started is ${started} and steps are ${JSON.stringify(steps)}`, () => {
    assert.equal(resumeStep(status(started, steps)), expected);
  });
}

const NEIGHBOURS = [
  ["welcome", -1, "welcome"],
  ["welcome", 1, "connection"],
  ["connection", -1, "welcome"],
  ["starter", 1, "done"],
  ["done", 1, "done"],
  ["done", -1, "starter"],
];

for (const [step, offset, expected] of NEIGHBOURS) {
  test(`moves from ${step} by ${offset} to ${expected}`, () => {
    assert.equal(neighbour(step, offset), expected);
  });
}
