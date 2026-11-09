---
name: pipeline-design
description: Design or change a data pipeline that can be rerun, backfilled and trusted, with raw, clean and served
  layers, a load strategy per table and a plan for late data. Use it for a new pipeline or ingestion, a new table, a
  backfill, or when a pipeline gave duplicates, gaps or doubtful numbers.
license: MIT
metadata:
  sources: agency-agents/engineering/engineering-data-engineer.md
---

# Pipeline design

Most pipeline failures are design failures: a load that duplicates on retry, a window that misses late rows, a
change nobody can rebuild. Decide these things before writing the first task.

## 1. Start from the readers

Ask, or find in the project: who reads the result, what one row means (the grain), how fresh it must be, and which
numbers it must agree with. A dashboard refreshed daily and an API that needs data within a minute lead to different
designs. Write the answers at the top of the design.

## 2. Know each source

For every source, find out:

- its key, and whether that key is stable;
- how it changes: append only, updates in place, deletes (hard or soft), or a change feed;
- which timestamp says when a row changed, and whether it can be trusted;
- how late rows can arrive, and whether old rows are ever corrected;
- its volume per day and its limits (rate limits, export windows).

What you cannot find out, write down as an assumption and check it with a query on the source.

## 3. Three layers

| Layer | Holds | Rules |
|---|---|---|
| raw | Source data as it arrived, plus `_loaded_at`, the source and the batch or file | Append only, never edited; kept long enough to rebuild from |
| clean | One row per entity or event, deduplicated, typed, named consistently | Rebuilt from raw; keys unique and not null |
| served | Tables shaped for one use: facts, dimensions, aggregates | Built from clean only; documented grain and owner |

Readers use the served layer only. When they read clean or raw, every internal change becomes their problem.

## 4. A load strategy per table

| Strategy | When | How it stays idempotent |
|---|---|---|
| Full refresh | Small tables, or sources without a change timestamp | Build into a new table and swap it in |
| Window replace | Facts by date or hour | Delete and insert the window in one transaction, or overwrite the partition |
| Merge on key | Entities that change in place | `MERGE` or upsert on the key, keeping the newest version |
| Change feed | Sources that offer one | Apply changes in order by the source's sequence; store the last position |

An incremental load reads from a watermark: the highest change timestamp it processed. Read with an overlap (for
example the last few hours again) and let the merge or window replace absorb the repeats. A watermark with no
overlap loses rows that arrive late or share a timestamp.

A window replace in plain SQL:

```sql
begin;
delete from clean.orders
where order_date >= :window_start and order_date < :window_end;
insert into clean.orders (order_id, customer_id, order_date, total_amount, _loaded_at)
select distinct on (r.order_id)
    r.order_id, r.customer_id, r.order_date, r.total_amount, now()
from raw.orders as r
where r.order_date >= :window_start and r.order_date < :window_end
order by r.order_id, r._loaded_at desc;
commit;
```

Running it twice for the same window leaves the same rows.

## 5. Late and corrected data

Decide per table what "late" means and how far back you reprocess. Common choices:

- reprocess a fixed lookback (the last three days) on every run;
- partition by event time and rerun a partition when late rows land in it;
- for aggregates, recompute the affected windows rather than adding to them.

Say in the table's documentation when a number becomes final.

## 6. Deletes

A source that deletes rows needs a plan, or deleted rows live on forever. Use the change feed's delete events, a
soft-delete column, or compare keys with a full extract on a slower schedule. Mark deleted rows (`_deleted_at`) in
clean rather than removing them, so history can be explained.

## 7. Backfills

A backfill uses the same code as the daily run, called with an older window. No separate script: two code paths
drift apart.

- Run it in chunks (a day or a month at a time), oldest first, with limited parallelism so the source and the
  warehouse are not overloaded.
- Compare row counts and totals per chunk with the source before moving on.
- Tell readers before a backfill changes numbers they have already seen.

## 8. Observe it

Every run records per table: rows read, rows written, the window, how long it took, and the checks that ran (see
the `data-quality` skill). Alert on a failed run, a failed check and data older than the freshness the readers need.

## In YAAP

- Write the design first and agree on it; build it one step at a time, and check each step before the next.
- Try every load on a small window in a development database, then run the same window again and compare counts.
- Record the layers, naming and source quirks in the project memory with `update_memory`.

## Design template

```
Readers and need: <who, grain, freshness, numbers it must match>
Sources: <per source: key, how it changes, change timestamp, lateness, volume>
Tables: <per table: layer, grain, key, load strategy, lookback>
Deletes: <how they are detected and marked>
Checks: <per layer, with severity>
Backfill: <chunk size, order, how each chunk is verified>
Assumptions: <what was not confirmed on the source>
```
