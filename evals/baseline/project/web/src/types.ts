// Shapes shared by the shop front end. Amounts are whole cents, as in the Python backend.

export type Category = "tools" | "fasteners" | "paint" | "garden" | "electrical" | "food" | "books";

export interface Product {
  sku: string;
  name: string;
  category: Category;
  priceCents: number;
  available: number;
}

export interface CartLine {
  sku: string;
  name: string;
  category: Category;
  unitPriceCents: number;
  quantity: number;
}

export type Delivery = "pickup" | "standard" | "express";

export interface CartTotals {
  subtotalCents: number;
  discountCents: number;
  vatCents: number;
  shippingCents: number;
  totalCents: number;
}

export interface CartProblem {
  sku: string;
  message: string;
}

export interface StoredCart {
  version: 1;
  lines: CartLine[];
  discountCode: string;
  delivery: Delivery;
}

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
