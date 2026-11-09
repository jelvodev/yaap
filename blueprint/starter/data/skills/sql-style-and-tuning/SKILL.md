---
name: sql-style-and-tuning
description: Write SQL that is easy to read and review, and make slow queries fast by reading the plan first. Use it
  whenever you write or review a query, view or model, when a query or page is slow, when the same query runs many
  times in a row (N+1), or before adding an index.
license: MIT
metadata:
  sources: wshobson/plugins/developer-essentials/skills/sql-optimization-patterns, agency-agents/engineering/engineering-database-optimizer.md
---

# SQL style and tuning

Readable SQL is reviewed well and breaks less. Fast SQL comes from reading the plan, not from rules of thumb. The
examples use PostgreSQL; other databases have the same ideas with other commands.

## Style

- **Name every column.** No `SELECT *` outside a quick look: it reads more than needed, defeats covering indexes and
  breaks readers when a column is added.
- **One clause per line,** keywords in one consistent case (follow the project; lowercase is fine), and indent the
  contents of each clause.
- **CTEs instead of nested subqueries.** Each `WITH` step has a name that says what it holds (`paid_orders`, not
  `t1`). Read top to bottom, the query tells its story.
- **Explicit joins and aliases.** Write `INNER JOIN` or `LEFT JOIN` with `ON`, never a comma join. Qualify every
  column with a short alias once more than one table is involved.
- **State the grain.** A comment above a query or model says what one row means ("one row per order line").
- **Parameters, not string building.** Values from users go in as bind parameters, never pasted into the text; that
  prevents SQL injection and lets the database reuse the plan.

```sql
-- One row per customer with their paid revenue in the window.
with paid_orders as (
    select o.customer_id, o.total_amount
    from orders as o
    where o.status = 'paid'
      and o.paid_at >= :window_start
      and o.paid_at < :window_end
)
select c.customer_id, c.country, sum(p.total_amount) as paid_revenue
from customers as c
inner join paid_orders as p on p.customer_id = c.customer_id
group by c.customer_id, c.country;
```

## Tuning: measure first

1. **Get the real query** with its parameters, from the log, the slow query log or `pg_stat_statements`. Note how
   often it runs: a 20 ms query run 5,000 times a minute matters more than a 2 s report run once a day.
2. **Read the plan** with `EXPLAIN (ANALYZE, BUFFERS)`. `ANALYZE` runs the statement: wrap an `UPDATE`, `DELETE`
   or `INSERT` in `BEGIN; … ROLLBACK;`, and never run it on production without the user's yes.
3. **Find the expensive node,** then ask why. Typical answers:
   - estimated rows far from actual rows: statistics are stale (`ANALYZE the_table`) or columns are correlated;
   - a sequential scan of a large table for a filter that keeps few rows: a missing or unusable index;
   - a nested loop over thousands of outer rows: a missing index on the inner side, or a bad estimate;
   - a sort or hash that spills to disk: too many rows reach it; filter earlier.
4. **Change one thing,** run the plan again and compare time and buffers. Keep the before and after for the report.

## Indexes

- Index the columns you filter and join on, including every foreign key.
- In a composite index, put columns compared with `=` first and the range column last: `(customer_id, created_at)`
  serves `where customer_id = ? and created_at >= ?`.
- A partial index (`where status = 'open'`) is small and fast when queries always use that condition.
- `INCLUDE` columns let a query be answered from the index alone.
- Every index slows writes and takes space. Drop indexes that `pg_stat_user_indexes` shows are never used.
- On a live table, create indexes with `CREATE INDEX CONCURRENTLY` so writes are not blocked; it cannot run inside a
  transaction, so it goes in its own migration step.

## Predicates the index can use

A condition can only use an index when the indexed column stands alone on one side:

```sql
-- Cannot use an index on created_at
where date(created_at) = '2026-11-01'
-- Can
where created_at >= '2026-11-01' and created_at < '2026-11-02'
```

The same goes for `lower(email) = ?` (index the expression or store the value normalised), a leading wildcard
(`like '%son'`), and comparing a column with a value of another type, which forces a cast per row.

## N+1 queries

The pattern: one query fetches a list, then one more query runs per item. It shows in the log as the same statement
repeated with a different id. Fix it with one query for the whole list: a join, or a batch fetch.

```sql
select customer_id, name
from customers
where customer_id = any(:customer_ids);
```

In an ORM, use its eager loading for the relation instead of touching it in a loop. Add a test that counts queries
for the endpoint, so the pattern does not return.

## Paging

`OFFSET 100000` reads and throws away 100,000 rows. For deep pages, page by key: remember the last row's sort values
and continue after them.

```sql
select order_id, created_at, total_amount
from orders
where (created_at, order_id) < (:last_created_at, :last_order_id)
order by created_at desc, order_id desc
limit 50;
```

This needs an index on `(created_at, order_id)`.

## In YAAP

- Run queries with `run_command` against a development database. Ask before touching a shared or production one.
- When `delegate` is available, hand a wide search for a query's callers ("where is this repository method used?")
  to an exploring worker; otherwise use `find_references`.
- Put an index change in a migration, not in a manual statement, so every environment gets it.

## Report

```
Query: <where it comes from, how often it runs>
Before: <time, buffers, the expensive plan node>
Cause: <one sentence>
Change: <the index, rewrite or setting, and the migration file>
After: <time, buffers>
Cost: <write overhead, size, or "none">
Not verified: <for example the effect on production data volumes>
```
