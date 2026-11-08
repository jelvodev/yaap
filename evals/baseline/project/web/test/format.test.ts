import assert from "node:assert/strict";
import { test } from "node:test";

import { formatCount, formatDeliveryDate, formatMoney, formatShare } from "../src/format.ts";

test("money has a euro sign, thousands separators and two decimals", () => {
  assert.equal(formatMoney(123456), "€1,234.56");
  assert.equal(formatMoney(5), "€0.05");
  assert.equal(formatMoney(100000000), "€1,000,000.00");
  assert.throws(() => formatMoney(1.5));
});

test("counts use the plural from two on", () => {
  assert.equal(formatCount(1), "1 item");
  assert.equal(formatCount(3), "3 items");
  assert.equal(formatCount(2, "box", "boxes"), "2 boxes");
});

test("shares have at most one decimal", () => {
  assert.equal(formatShare(0.215), "21.5%");
  assert.equal(formatShare(1), "100%");
});

test("delivery dates are short", () => {
  assert.equal(formatDeliveryDate(new Date(Date.UTC(2026, 2, 3))), "Tue 3 Mar");
});
