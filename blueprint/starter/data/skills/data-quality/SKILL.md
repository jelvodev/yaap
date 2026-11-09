---
name: data-quality
description: Add data quality checks to each layer of a pipeline and decide what happens when one fails, so bad data
  stops before anyone reads it. Use this whenever you build or change a pipeline or model, add tests to tables, set
  up dbt tests or another checking tool, when numbers look wrong, a dashboard disagrees with the source, or a check
  failed and you need to decide whether to stop, quarantine or let it pass.
license: MIT
metadata:
  sources: wshobson/plugins/data-engineering/skills/data-quality-frameworks
---

# Data quality

A check is only useful when it runs on every load and something happens when it fails. Write few checks on the
columns that matter, give each a severity, and decide in advance what a failure does.

## What to check, per layer

| Layer | Checks | Why |
|---|---|---|
| raw | The load arrived; row count within the usual range; the columns and types are the expected ones; the newest `_loaded_at` is recent | Catches a broken or partial delivery before it is transformed |
| clean | Keys unique and not null; required columns not null; values in their allowed set or range; foreign keys point to existing rows | Catches duplicates and broken joins, the most common cause of wrong numbers |
| served | Totals reconcile with clean or the source; business rules hold (a refund never exceeds its order); volume compared with the same day last week | Catches logic errors that every row on its own passes |

Start with the keys and the freshness of each table. Add a check when a problem was found, so it cannot return
unnoticed.

## Writing a check

A check is a query that returns the rows that break the rule; zero rows means it passed. Every tool (dbt tests,
Great Expectations, Soda, plain SQL in the pipeline) works this way underneath, so use the one the project has.

```sql
-- Error when it returns rows: orders whose customer does not exist.
select o.order_id
from clean.orders as o
left join clean.customers as c on c.customer_id = o.customer_id
where c.customer_id is null;
```

A volume check compares with history rather than a fixed number, which goes stale:

```sql
-- Warn when today's rows fall below half the average of the last 28 days.
with daily as (
    select order_date, count(*) as row_count
    from clean.orders
    where order_date >= current_date - 28
    group by order_date
)
select t.row_count, avg(h.row_count) as usual
from daily as t
cross join daily as h
where t.order_date = current_date
  and h.order_date < current_date
group by t.row_count
having t.row_count < 0.5 * avg(h.row_count);
```

When no rows arrived today at all, this query returns nothing; the freshness check on raw covers that case.

## Severity

- **error**: the data is wrong for its readers. Stop the downstream tasks.
- **warn**: unusual but possibly right (a quiet holiday). Report it and let the run continue.

Give a check a threshold when a few bad rows are acceptable ("at most 0.1% of rows without a country"), and say why
in the check's description.

## When a check fails

1. **Stop what reads from it.** Downstream tasks do not run; readers keep yesterday's correct data rather than
   today's wrong data.
2. **Tell the owner** what failed: the check, the table, the window, the number of failing rows and a few keys.
   Never put personal data from the rows in the alert.
3. **Find the cause.** Compare with the source: did the source change, was the delivery partial, or is the
   transformation wrong? Follow the `systematic-debugging` method when the cause is not obvious.
4. **Fix and rerun the window.** Because loads are idempotent, rerunning the same window after the fix is safe.
5. **Quarantine only on purpose.** Moving failing rows to a side table and loading the rest is right when a few bad
   rows must not hold back the rest; then the quarantine table gets its own alert and an owner.

Never make a failing check pass by loosening it, raising its threshold or deleting it without a reason in the
change. A check that fails for a good reason is a decision for the user.

## Contracts between teams

When another team produces the data, agree on a contract: the columns, types, key, freshness and who is told about a
change. Check the contract at the raw layer, so a broken delivery fails at the door with the producer named.

## In YAAP

- Run checks with `run_command` against a development database and quote the result.
- Use synthetic rows in tests and fixtures; when you show failing rows in a reply, show keys and counts.
- Record known data issues and their owners in the project memory with `update_memory`.

## Report

```
Checks: <added or changed, per table, with severity>
Result: <passed / failed, with counts>
Failure: <check, window, failing rows, cause, or "none">
Action: <stopped, quarantined, fixed and rerun>
Not verified: <for example checks that need production volumes>
```
