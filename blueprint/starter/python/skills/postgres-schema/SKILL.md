---
name: postgres-schema
description: Design or review PostgreSQL tables with the right column types, constraints that keep bad data out and
  the indexes the queries need. Use it whenever you create or change a table, choose a type, add a foreign key or
  index, model money, time, status or JSON, or review an ORM model.
license: MIT
metadata:
  sources: wshobson/plugins/database-design/skills/postgresql-table-design
---

# PostgreSQL schema

The schema is the last line of defence for your data. Application checks have bugs and get bypassed by scripts and
other services; a constraint holds for every writer. Make the database refuse what must never be stored.

## 1. Model first

Normalise to third normal form: each fact in one place. Denormalise only for a read you measured as too slow, and
write down which copy is the source. Names are `snake_case`, unquoted, tables plural (`orders`), foreign keys
`<thing>_id`.

## 2. Types

| Data | Use | Avoid |
|---|---|---|
| Ids | `bigint GENERATED ALWAYS AS IDENTITY`; `uuid` when ids are made outside the database or must not be guessable | `serial`, `integer` that runs out |
| Text | `text`, with a `CHECK (length(col) <= n)` when there is a real limit | `varchar(255)` out of habit, `char(n)` |
| Money and exact numbers | `numeric(12, 2)`, or `bigint` in minor units | `real`, `double precision`, the `money` type |
| Moments in time | `timestamptz` | `timestamp` without a time zone |
| Dates | `date` | text |
| Yes or no | `boolean NOT NULL` | a nullable boolean, unless "unknown" is a real third value |
| Status with a few stable values | `text` with a `CHECK (status IN (…))`, or a lookup table | a free string with no check |
| Optional, varying attributes | `jsonb`, for the parts you do not filter on often | `json`, or `jsonb` for core fields |

A native `enum` type is fine for a small set that never changes; removing a value from one is hard.

## 3. Constraints

- **`NOT NULL` by default.** Make a column nullable only when "no value" is a real state, and know what it means.
- **Primary key on every table.**
- **Foreign keys for every reference,** with a deliberate `ON DELETE`: `RESTRICT` (the default choice), `CASCADE` for
  rows that cannot exist without their parent, `SET NULL` when the link is optional.
- **`UNIQUE` for natural keys** (an email, an order number). A unique column allows many `NULL`s; use
  `UNIQUE NULLS NOT DISTINCT` (PostgreSQL 15 and later) when one `NULL` is the limit.
- **`CHECK` for rules on one row** (`total >= 0`, `ends_at > starts_at`). A `CHECK` lets `NULL` pass, so pair it with
  `NOT NULL` where needed.
- **Exclusion constraints** for "no overlap" rules, such as two bookings of one room at the same time.

## 4. Indexes

An index costs space and slows every write. Add one for a query that needs it, and check with `EXPLAIN ANALYZE` that
it is used.

- **Every foreign key column.** PostgreSQL does not index them for you, and joins and cascading deletes need them.
- **Composite indexes** follow the left-most rule: `(customer_id, created_at)` serves a filter on `customer_id`, or on
  both, but not on `created_at` alone. Put the equality columns first, the range or sort column last.
- **Partial indexes** for a small hot subset: `WHERE status = 'open'`.
- **Expression indexes** for a computed lookup, such as `lower(email)`; the query must use the same expression.
- **GIN** for `jsonb` containment, arrays and full-text search; **BRIN** for very large tables ordered by time.

## 5. Example

```sql
CREATE TABLE orders (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customers (id) ON DELETE RESTRICT,
    number      text NOT NULL UNIQUE,
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'paid', 'cancelled')),
    total       numeric(12, 2) NOT NULL CHECK (total >= 0),
    currency    char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    note        text CHECK (length(note) <= 2000),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX orders_customer_id_created_at_idx ON orders (customer_id, created_at);
CREATE INDEX orders_open_created_at_idx ON orders (created_at) WHERE status = 'open';
```

`char(3)` is one of the few good uses of a fixed length: a currency code is always three letters.

## 6. Things that surprise people

- Identity and sequence values have gaps after rollbacks; that is normal, do not try to fill them.
- `now()` is the start of the transaction, the same for every row in it; `clock_timestamp()` is the wall clock.
- A value too long for its type fails instead of being cut off; that is a feature.
- Updates leave dead rows that vacuum cleans up; a table where wide rows change constantly needs attention.
- Names over 63 characters are truncated with only a NOTICE, which is easy to miss in a migration log, so two long
  names can end up the same; keep index and constraint names short and explicit.

## 7. Keep the schema in migrations

Every schema change goes through a migration in the repository, and the tests check that the migrated schema matches
the models. Changing a table that already holds data is a job for the `safe-migrations` skill.

## Report

For a new or reviewed table:

```
Table: <name and what one row is>
Keys: <primary key, natural keys, foreign keys with ON DELETE>
Checks: <the rules the database enforces>
Indexes: <each with the query it serves>
Open questions: <nullability or delete behaviour the user must decide>
```
