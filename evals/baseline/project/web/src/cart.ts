// The shopping cart of the shop front end.
//
// The cart keeps lines per SKU and price, checks quantities against what the stockroom says is available, applies
// discount codes with the same rules as the backend (stockroom/orders.py), and computes VAT per line on the
// discounted amount, again as the backend does, so the total the customer sees is the total of the order. It is
// saved to a key-value store (localStorage in the browser) after every change.

import { formatCount, formatMoney } from "./format.ts";
import type {
  CartLine,
  CartProblem,
  CartTotals,
  Category,
  Delivery,
  KeyValueStore,
  Product,
  StoredCart,
} from "./types.ts";

export const STORAGE_KEY = "stockroom.cart";
export const MAX_LINES = 50;
export const MAX_QUANTITY = 999;

// code -> [percentage off, minimum subtotal in cents]; mirrors DISCOUNT_RULES in stockroom/orders.py.
export const DISCOUNT_RULES: Record<string, readonly [number, number]> = {
  WELCOME10: [10, 0],
  BULK15: [15, 50_000],
  STAFF25: [25, 0],
};

const VAT_BY_CATEGORY: Record<Category, number> = {
  tools: 21,
  fasteners: 21,
  paint: 21,
  garden: 21,
  electrical: 21,
  food: 9,
  books: 9,
};

export const SHIPPING_CENTS: Record<Delivery, number> = {
  pickup: 0,
  standard: 495,
  express: 1295,
};
export const FREE_SHIPPING_FROM_CENTS = 5_000;

export class CartError extends Error {}

type Listener = (cart: Cart) => void;

export class Cart {
  private lines: CartLine[] = [];
  private discountCode = "";
  private delivery: Delivery = "standard";
  private listeners: Listener[] = [];
  private readonly store: KeyValueStore | null;

  constructor(store: KeyValueStore | null = null) {
    this.store = store;
  }

  // --- Reading -----------------------------------------------------------------------------------------------

  get items(): readonly CartLine[] {
    return this.lines;
  }

  get code(): string {
    return this.discountCode;
  }

  get deliveryMethod(): Delivery {
    return this.delivery;
  }

  get isEmpty(): boolean {
    return this.lines.length === 0;
  }

  get unitCount(): number {
    return this.lines.reduce((sum, line) => sum + line.quantity, 0);
  }

  quantityOf(sku: string): number {
    return this.lines.filter((line) => line.sku === sku).reduce((sum, line) => sum + line.quantity, 0);
  }

  // --- Changing ----------------------------------------------------------------------------------------------

  add(product: Product, quantity = 1): CartLine {
    checkQuantity(quantity);
    const inCart = this.quantityOf(product.sku);
    if (inCart + quantity > product.available) {
      throw new CartError(
        `Only ${formatCount(product.available)} of ${product.name} available; ${inCart} already in the cart.`,
      );
    }
    const existing = this.lines.find(
      (line) => line.sku === product.sku && line.unitPriceCents === product.priceCents,
    );
    if (existing) {
      checkQuantity(existing.quantity + quantity);
      existing.quantity += quantity;
      this.changed();
      return existing;
    }
    if (this.lines.length >= MAX_LINES) {
      throw new CartError(`A cart holds at most ${MAX_LINES} lines.`);
    }
    const line: CartLine = {
      sku: product.sku,
      name: product.name,
      category: product.category,
      unitPriceCents: product.priceCents,
      quantity,
    };
    this.lines.push(line);
    this.changed();
    return line;
  }

  setQuantity(sku: string, quantity: number, available?: number): void {
    const line = this.requireLine(sku);
    if (quantity === 0) {
      this.remove(sku);
      return;
    }
    checkQuantity(quantity);
    if (available !== undefined && quantity > available) {
      throw new CartError(`Only ${formatCount(available)} of ${line.name} available.`);
    }
    line.quantity = quantity;
    this.changed();
  }

  remove(sku: string): void {
    const before = this.lines.length;
    this.lines = this.lines.filter((line) => line.sku !== sku);
    if (this.lines.length !== before) {
      this.changed();
    }
  }

  clear(): void {
    this.lines = [];
    this.discountCode = "";
    this.changed();
  }

  applyCode(code: string): void {
    const normalized = code.trim().toUpperCase();
    if (!normalized) {
      this.discountCode = "";
      this.changed();
      return;
    }
    discountCents(this.subtotalCents(), normalized);
    this.discountCode = normalized;
    this.changed();
  }

  setDelivery(delivery: Delivery): void {
    this.delivery = delivery;
    this.changed();
  }

  // --- Totals ------------------------------------------------------------------------------------------------

  subtotalCents(): number {
    return this.lines.reduce((sum, line) => sum + line.unitPriceCents * line.quantity, 0);
  }

  totals(): CartTotals {
    const subtotal = this.subtotalCents();
    const discount = this.discountCode ? safeDiscount(subtotal, this.discountCode) : 0;
    let vat = 0;
    for (const line of this.lines) {
      const amount = line.unitPriceCents * line.quantity;
      const share = subtotal ? amount - Math.floor((amount * discount) / subtotal) : amount;
      vat += vatCents(share, VAT_BY_CATEGORY[line.category]);
    }
    const shipping = shippingCents(this.delivery, subtotal - discount);
    return {
      subtotalCents: subtotal,
      discountCents: discount,
      vatCents: vat,
      shippingCents: shipping,
      totalCents: subtotal - discount + vat + shipping,
    };
  }

  summary(): string[] {
    const totals = this.totals();
    const rows = this.lines.map(
      (line) => `${line.quantity} × ${line.name}  ${formatMoney(line.unitPriceCents * line.quantity)}`,
    );
    rows.push(`Subtotal  ${formatMoney(totals.subtotalCents)}`);
    if (totals.discountCents) {
      rows.push(`Discount ${this.discountCode}  -${formatMoney(totals.discountCents)}`);
    }
    rows.push(`VAT  ${formatMoney(totals.vatCents)}`);
    rows.push(`Shipping (${this.delivery})  ${formatMoney(totals.shippingCents)}`);
    rows.push(`Total  ${formatMoney(totals.totalCents)}`);
    return rows;
  }

  // --- Checking against the stockroom ------------------------------------------------------------------------

  /** Lines that no longer fit what the stockroom has: gone, fewer available, or another price. */
  problems(products: readonly Product[]): CartProblem[] {
    const bySku = new Map(products.map((product) => [product.sku, product]));
    const found: CartProblem[] = [];
    for (const line of this.lines) {
      const product = bySku.get(line.sku);
      if (!product) {
        found.push({ sku: line.sku, message: `${line.name} is no longer sold.` });
        continue;
      }
      if (product.available === 0) {
        found.push({ sku: line.sku, message: `${line.name} is sold out.` });
      } else if (this.quantityOf(line.sku) > product.available) {
        found.push({ sku: line.sku, message: `Only ${formatCount(product.available)} of ${line.name} left.` });
      }
      if (product.priceCents !== line.unitPriceCents) {
        found.push({
          sku: line.sku,
          message: `The price of ${line.name} changed to ${formatMoney(product.priceCents)}.`,
        });
      }
    }
    return found;
  }

  /** Bring the cart in line with the stockroom: drop what is gone, lower quantities, take the new prices. */
  reconcile(products: readonly Product[]): CartProblem[] {
    const problems = this.problems(products);
    if (problems.length === 0) {
      return problems;
    }
    const bySku = new Map(products.map((product) => [product.sku, product]));
    const kept: CartLine[] = [];
    for (const line of this.lines) {
      const product = bySku.get(line.sku);
      if (!product || product.available === 0) {
        continue;
      }
      const room = product.available - kept.filter((k) => k.sku === line.sku).reduce((s, k) => s + k.quantity, 0);
      if (room <= 0) {
        continue;
      }
      kept.push({ ...line, unitPriceCents: product.priceCents, quantity: Math.min(line.quantity, room) });
    }
    this.lines = mergeLines(kept);
    if (this.discountCode && safeDiscount(this.subtotalCents(), this.discountCode) === 0) {
      this.discountCode = "";
    }
    this.changed();
    return problems;
  }

  // --- Saving ------------------------------------------------------------------------------------------------

  toStored(): StoredCart {
    return {
      version: 1,
      lines: this.lines.map((line) => ({ ...line })),
      discountCode: this.discountCode,
      delivery: this.delivery,
    };
  }

  static fromStored(data: unknown, store: KeyValueStore | null = null): Cart {
    const cart = new Cart(store);
    if (!isStoredCart(data)) {
      return cart;
    }
    cart.lines = mergeLines(data.lines.filter(isValidLine).slice(0, MAX_LINES));
    cart.discountCode = data.discountCode;
    cart.delivery = data.delivery;
    return cart;
  }

  static load(store: KeyValueStore): Cart {
    const raw = store.getItem(STORAGE_KEY);
    if (raw === null) {
      return new Cart(store);
    }
    try {
      return Cart.fromStored(JSON.parse(raw), store);
    } catch {
      // A broken saved cart is dropped; the customer starts with an empty one.
      store.removeItem(STORAGE_KEY);
      return new Cart(store);
    }
  }

  onChange(listener: Listener): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((other) => other !== listener);
    };
  }

  // --- Internals ---------------------------------------------------------------------------------------------

  private requireLine(sku: string): CartLine {
    const line = this.lines.find((candidate) => candidate.sku === sku);
    if (!line) {
      throw new CartError(`${sku} is not in the cart.`);
    }
    return line;
  }

  private changed(): void {
    this.store?.setItem(STORAGE_KEY, JSON.stringify(this.toStored()));
    for (const listener of this.listeners) {
      listener(this);
    }
  }
}

// --- Rules shared with the backend ---------------------------------------------------------------------------

export function discountCents(subtotalCents: number, code: string): number {
  const rule = DISCOUNT_RULES[code.trim().toUpperCase()];
  if (!rule) {
    throw new CartError(`Unknown discount code ${code}.`);
  }
  const [percent, minimum] = rule;
  if (subtotalCents < minimum) {
    throw new CartError(`${code.toUpperCase()} needs a subtotal of at least ${formatMoney(minimum)}.`);
  }
  return Math.floor((subtotalCents * percent) / 100);
}

/** The discount, or 0 when the code no longer applies (the subtotal dropped below its minimum). */
export function safeDiscount(subtotalCents: number, code: string): number {
  try {
    return discountCents(subtotalCents, code);
  } catch {
    return 0;
  }
}

export function vatCents(netCents: number, ratePercent: number): number {
  return Math.round((netCents * ratePercent) / 100);
}

export function shippingCents(delivery: Delivery, netCents: number): number {
  if (delivery === "standard" && netCents >= FREE_SHIPPING_FROM_CENTS) {
    return 0;
  }
  return SHIPPING_CENTS[delivery];
}

function checkQuantity(quantity: number): void {
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > MAX_QUANTITY) {
    throw new CartError(`A quantity is a whole number from 1 to ${MAX_QUANTITY}.`);
  }
}

function mergeLines(lines: CartLine[]): CartLine[] {
  const merged: CartLine[] = [];
  for (const line of lines) {
    const same = merged.find((m) => m.sku === line.sku && m.unitPriceCents === line.unitPriceCents);
    if (same) {
      same.quantity = Math.min(same.quantity + line.quantity, MAX_QUANTITY);
    } else {
      merged.push({ ...line });
    }
  }
  return merged;
}

function isStoredCart(data: unknown): data is StoredCart {
  if (typeof data !== "object" || data === null) {
    return false;
  }
  const candidate = data as Partial<StoredCart>;
  return (
    candidate.version === 1 &&
    Array.isArray(candidate.lines) &&
    typeof candidate.discountCode === "string" &&
    (candidate.delivery === "pickup" || candidate.delivery === "standard" || candidate.delivery === "express")
  );
}

function isValidLine(line: unknown): line is CartLine {
  if (typeof line !== "object" || line === null) {
    return false;
  }
  const candidate = line as Partial<CartLine>;
  return (
    typeof candidate.sku === "string" &&
    typeof candidate.name === "string" &&
    typeof candidate.category === "string" &&
    candidate.category in VAT_BY_CATEGORY &&
    Number.isInteger(candidate.unitPriceCents) &&
    Number.isInteger(candidate.quantity) &&
    (candidate.quantity ?? 0) >= 1
  );
}
