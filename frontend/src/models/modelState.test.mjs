// Tests of the Models list's wording (npm run test:unit). Node strips the types of modelState.ts as it loads it.

import assert from "node:assert/strict";
import { test } from "node:test";

import { anyDownloading, percent, sizeText, stateText } from "./modelState.ts";

const model = (state, downloaded = 0, size = 2_000_000_000) => ({
  name: "bge-m3",
  label: "Search model",
  purpose: "Search by meaning",
  state,
  size,
  downloaded,
  message: "",
});

test("sizes use decimal units", () => {
  assert.equal(sizeText(2_287_000_000), "2.3 GB");
  assert.equal(sizeText(486_000_000), "486 MB");
  assert.equal(sizeText(65_552), "66 KB");
});

test("the percentage stays between 0 and 100", () => {
  assert.equal(percent(model("downloading", 500_000_000)), 25);
  assert.equal(percent(model("downloading", 3_000_000_000)), 100);
  assert.equal(percent(model("downloading", 5, 0)), 0);
});

test("each state has its words", () => {
  assert.equal(stateText(model("not_downloaded")), "not downloaded");
  assert.equal(stateText(model("downloading", 1_000_000_000)), "downloading, 50% of 2.0 GB");
  assert.equal(stateText(model("ready")), "ready");
  assert.equal(stateText(model("failed")), "failed");
});

test("the list is read again only while a download runs", () => {
  assert.equal(anyDownloading([model("ready"), model("downloading")]), true);
  assert.equal(anyDownloading([model("ready"), model("failed")]), false);
});
