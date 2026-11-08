import assert from "node:assert/strict";
import { test } from "node:test";

import { Cart, CartError, STORAGE_KEY, discountCents, shippingCents } from "../src/cart.ts";
import type { KeyValueStore, Product } from "../src/types.ts";

const hammer: Product = { sku: "SR-1", name: "Claw hammer", category: "tools", priceCents: 2000, available: 5 };
const coffee: Product = { sku: "SR-3", name: "Coffee beans", category: "food", priceCents: 1500, available: 10 };

class MemoryStore implements KeyValueStore {
  values = new Map<string, string>();
  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
  removeItem(key: string): void {
    this.values.delete(key);
  }
}

test("adding the same product merges the lines and respects what is available", () => {
  const cart = new Cart();
  cart.add(hammer, 2);
  cart.add(hammer, 1);
  assert.equal(cart.items.length, 1);
  assert.equal(cart.quantityOf("SR-1"), 3);
  assert.throws(() => cart.add(hammer, 3), CartError);
  assert.throws(() => cart.add(coffee, 0), CartError);
});

test("totals apply the discount before VAT per line, like the backend", () => {
  const cart = new Cart();
  cart.add(hammer, 2);
  cart.add(coffee, 1);
  cart.applyCode("welcome10");
  const totals = cart.totals();
  assert.equal(totals.subtotalCents, 5500);
  assert.equal(totals.discountCents, 550);
  assert.equal(totals.vatCents, 756 + 122);
  assert.equal(totals.shippingCents, 495);
  assert.equal(totals.totalCents, 4950 + 878 + 495);
});

test("standard shipping is free from fifty euros, express never", () => {
  assert.equal(shippingCents("standard", 5000), 0);
  assert.equal(shippingCents("express", 9000), 1295);
  assert.equal(shippingCents("pickup", 100), 0);
});

test("discount codes have a minimum subtotal", () => {
  assert.equal(discountCents(10000, "STAFF25"), 2500);
  assert.throws(() => discountCents(10000, "BULK15"), CartError);
  assert.throws(() => discountCents(10000, "NOPE"), CartError);
});

test("reconciling drops sold-out lines, lowers quantities and takes new prices", () => {
  const cart = new Cart();
  cart.add(hammer, 4);
  cart.add(coffee, 2);
  const problems = cart.reconcile([
    { ...hammer, available: 2, priceCents: 2200 },
    { ...coffee, available: 0 },
  ]);
  assert.equal(problems.length, 3);
  assert.deepEqual(
    cart.items.map((line) => [line.sku, line.quantity, line.unitPriceCents]),
    [["SR-1", 2, 2200]],
  );
});

test("the cart is saved after every change and loaded again", () => {
  const store = new MemoryStore();
  const cart = new Cart(store);
  let changes = 0;
  const stop = cart.onChange(() => changes++);
  cart.add(hammer, 1);
  cart.setDelivery("pickup");
  stop();
  cart.setQuantity("SR-1", 2);
  assert.equal(changes, 2);
  const loaded = Cart.load(store);
  assert.equal(loaded.quantityOf("SR-1"), 2);
  assert.equal(loaded.deliveryMethod, "pickup");
  store.setItem(STORAGE_KEY, "{broken");
  assert.equal(Cart.load(store).isEmpty, true);
  assert.equal(store.getItem(STORAGE_KEY), null);
});

test("the summary lists lines and totals", () => {
  const cart = new Cart();
  cart.add(coffee, 2);
  cart.applyCode("STAFF25");
  const rows = cart.summary();
  assert.equal(rows[0], "2 × Coffee beans  €30.00");
  assert.ok(rows.includes("Discount STAFF25  -€7.50"));
  assert.equal(cart.unitCount, 2);
  cart.remove("SR-3");
  cart.clear();
  assert.equal(cart.code, "");
});
