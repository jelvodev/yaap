// Tests of the model setup's label and context ring (npm run test:unit). Node strips the types of modelSetup.ts.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  contextText,
  contextUse,
  formatTokens,
  goalText,
  ringDash,
  setupLabel,
  shortModelName,
} from "./modelSetup.ts";

test("the label names the agent, the model's short name and the effort", () => {
  assert.equal(setupLabel("", "Opus 5.5", "medium"), "YAAP · Opus 5.5 · medium");
  assert.equal(setupLabel(null, "Opus 5.5", "medium"), "YAAP · Opus 5.5 · medium");
  assert.equal(setupLabel("reviewer", "GPT-5.2", "high"), "reviewer · GPT-5.2 · high");
});

test("a model without effort leaves the effort out of the label", () => {
  assert.equal(setupLabel("", "Llama 4", ""), "YAAP · Llama 4");
  assert.equal(setupLabel("", "Llama 4", null), "YAAP · Llama 4");
});

test("the short name drops the family word Claude and keeps other names", () => {
  assert.equal(shortModelName("Claude Opus 5.5", "claude-opus-5-5"), "Opus 5.5");
  assert.equal(shortModelName("GPT-5.2", "gpt-5.2"), "GPT-5.2");
  assert.equal(shortModelName("Claude", "claude"), "Claude");
});

test("without a display name the short name is the model id", () => {
  assert.equal(shortModelName(undefined, "local-model"), "local-model");
  assert.equal(shortModelName("  ", "local-model"), "local-model");
});

test("the fraction is the share of the limit, else of the window", () => {
  assert.equal(contextUse(50_000, 100_000, 1_000_000).fraction, 0.5);
  assert.equal(contextUse(250_000, null, 1_000_000).fraction, 0.25);
  assert.equal(contextUse(null, 100_000, 1_000_000).fraction, 0);
  assert.equal(contextUse(10, null, null).fraction, 0);
  assert.equal(contextUse(300_000, 200_000, null).fraction, 1);
});

test("the colour state turns at 80% and 95%", () => {
  assert.equal(contextUse(79_999, 100_000, null).state, "ok");
  assert.equal(contextUse(80_000, 100_000, null).state, "filling");
  assert.equal(contextUse(94_999, 100_000, null).state, "filling");
  assert.equal(contextUse(95_000, 100_000, null).state, "full");
});

test("the context text shows the window only when the limit is lower", () => {
  assert.equal(contextText(contextUse(33_000, 500_000, 1_000_000)), "33k / 500k of 1.0M");
  assert.equal(contextText(contextUse(33_000, null, 1_000_000)), "33k / 1.0M");
  assert.equal(contextText(contextUse(null, null, null)), "0 / 0");
  assert.equal(formatTokens(950), "950");
});

test("the ring's dash fills the fraction of the circle", () => {
  const { length, dash } = ringDash(0.25, 10);
  assert.equal(length, 2 * Math.PI * 10);
  assert.equal(dash, `${(length / 4).toFixed(2)} ${length.toFixed(2)}`);
  assert.equal(ringDash(2, 10).dash.split(" ")[0], length.toFixed(2));
  assert.equal(ringDash(-1, 10).dash.split(" ")[0], "0.00");
});

test("goal mode puts /goal before the text once", () => {
  assert.equal(goalText(""), "/goal ");
  assert.equal(goalText("  ship the login page"), "/goal ship the login page");
  assert.equal(goalText("/goal ship it"), "/goal ship it");
  assert.equal(goalText("/goal"), "/goal ");
  assert.equal(goalText("/goals for later"), "/goal /goals for later");
});
