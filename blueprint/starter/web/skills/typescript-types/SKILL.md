---
name: typescript-types
description: Use TypeScript's advanced types where they prevent real bugs (unknown and narrowing, discriminated
  unions, exhaustive checks, satisfies, branded ids, generics with constraints) and stop before types become a puzzle.
  Use this whenever you type API data, model a value with several states, are tempted to write any, a type assertion
  or a non-null assertion, write a generic helper, or fight a confusing type error.
license: MIT
metadata:
  sources: wshobson/plugins/javascript-typescript/skills/typescript-advanced-types
---

# TypeScript types

Types are worth what they catch. Use the features below when they turn a likely bug into a compile error; skip them
when they only make the code look clever. A type a colleague cannot read in a minute is a cost.

## 1. unknown, then narrow

Data from outside (the network, `localStorage`, `JSON.parse`, a message event) is `unknown` until checked. `any`
turns the checker off for everything it touches.

```ts
type OrderSummary = { id: string; total: number };

function isOrderSummary(value: unknown): value is OrderSummary {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof value.id === "string" &&
    "total" in value &&
    typeof value.total === "number"
  );
}
```

For anything larger than a few fields, use the project's schema library and infer the type from the schema, so the
check and the type cannot drift apart.

## 2. One union for a value with states

A value that is loading, loaded or failed is one discriminated union, not three optional fields that allow
impossible combinations.

```ts
type Load<T> =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "done"; data: T }
  | { kind: "failed"; error: string };

function describe(load: Load<Order[]>): string {
  switch (load.kind) {
    case "idle":
      return "Not started";
    case "loading":
      return "Loading";
    case "done":
      return `${load.data.length} orders`;
    case "failed":
      return load.error;
    default: {
      const unreachable: never = load;
      return unreachable;
    }
  }
}
```

The `never` line makes adding a fifth state a compile error in every switch that forgot it.

## 3. Literal types and satisfies

`as const` keeps literal values; `satisfies` checks an object against a type without widening it, so you keep the
exact keys.

```ts
const routes = {
  home: "/",
  orders: "/orders",
} as const satisfies Record<string, `/${string}`>;

type RouteName = keyof typeof routes; // "home" | "orders"
```

Derive unions from such objects instead of writing the list twice.

## 4. Brand ids that must not mix

When several ids are all strings, a branded type stops passing an order id where a customer id belongs.

```ts
type OrderId = string & { readonly brand: "OrderId" };
type CustomerId = string & { readonly brand: "CustomerId" };

const toOrderId = (value: string): OrderId => value as OrderId;
```

Create branded values in one place (the parser of the API response), never ad hoc.

## 5. Generics with a constraint

A generic is useful when the output type depends on the input type. Constrain it so the body can use what it needs.

```ts
function byId<T extends { id: string }>(items: readonly T[]): Map<string, T> {
  return new Map(items.map((item) => [item.id, item]));
}
```

If a type parameter appears only once in the signature, it adds nothing; use the concrete type.

## 6. Utility types before custom ones

`Pick`, `Omit`, `Partial`, `Required`, `Readonly`, `Record`, `ReturnType` and `Awaited` cover most needs. Write a
mapped or conditional type only when they do not, and give it a name and a one-line comment saying what it is for.
Template literal types are good for a fixed set of strings (event names, CSS units); beyond that they slow the
compiler and the reader.

## 7. Assertions are a last resort

`value as T` and `value!` tell the compiler to trust you. Prefer a type guard, an early return or a default. When an
assertion is unavoidable (a DOM element you just created, a library with a loose type), keep it in one small function
with a comment. `@ts-expect-error` with a reason beats `@ts-ignore`, because it fails once the error is gone.

## 8. Test types that matter

For a helper type others rely on, add a type test: a line that must compile and one marked `@ts-expect-error` that
must not, or the test runner's `expectTypeOf`. Run the type check (`tsc --noEmit` or the project's script) with the
tests, and read `diagnostics` for the files you changed.

## When a type fights you

Read the whole error from the bottom up; the last lines name the mismatch. Hover or print the inferred type with a
temporary `type Check = typeof value`. If a type needs more than one conditional to express, the data model is often
the problem; simplify the data first.
