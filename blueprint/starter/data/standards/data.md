---
sources: [agency-agents/engineering/engineering-data-engineer.md, wshobson/plugins/data-engineering/skills/data-quality-frameworks, own]
category: data
summary: Pipelines that can be rerun safely, schemas treated as contracts, checks that stop bad data, and no personal
  data where it does not belong.
rules:
- Make every load idempotent; a rerun or backfill of the same window must give the same rows, never duplicates.
- Keep raw data unchanged and append-only, so every later layer can be rebuilt from it.
- Treat a table's schema as a contract; change it on purpose and fail on unexpected drift instead of guessing.
- Check data quality at each layer and stop the run on a failed check, so bad data never reaches its readers.
- Keep personal data out of samples, fixtures, logs and replies; use synthetic or masked data instead.
---

# Data

How to build and change data pipelines. The rules above are always in the prompt; this text explains them. The
skills `pipeline-design`, `data-quality`, `sql-style-and-tuning`, `dbt-models` and `airflow-dags` hold the methods.

## Idempotent loads

A pipeline will be rerun: after a failure, for a backfill, by a scheduler that retries. Each load therefore works on
an explicit window (a date, an hour, a batch id) and replaces exactly that window, or merges on a key. Appending
without a key, or deriving the window from "now", turns a retry into duplicates or gaps.

Test it: run the same window twice and compare the row count and a checksum. They must match.

## Raw stays raw

Land source data as it arrived, with load metadata (`_loaded_at`, the source, the file or batch). Do not clean or
fix it in place. When a transformation turns out to be wrong, you fix the code and rebuild from raw; without raw
the original is gone.

## Schemas are contracts

Readers of a table rely on its columns, types, grain (what one row means) and keys. Write these down next to the
model and keep them true. A change that removes or renames a column, changes a type or the grain is a breaking
change: announce it, add the new column first, move readers, then remove the old one.

When a source sends a column you did not expect or misses one you need, fail the load with a clear message. Silently
dropping or inferring columns spreads the problem to every table downstream.

## Checks stop the run

Every layer has checks that fit it (the `data-quality` skill lists them). A failed check with severity *error* stops
the downstream tasks and tells the owner what failed and how many rows. A *warning* is reported but does not stop the
run. Never make a failing check pass by loosening it without saying why in the change.

## Personal data

Names, email addresses, phone numbers, addresses, identifiers and free text from users are personal data. Keep them
out of:

- test fixtures and seed files: generate synthetic rows;
- samples you show in a reply or a report: show counts, keys or masked values;
- logs and error messages: log the key and the check, not the row.

When a pipeline must process personal data, keep it in as few columns and tables as possible, and say in the model's
documentation which columns hold it.

## In YAAP

- Work against a development database or a sample, never production, unless the user asks and the change is
  read-only.
- Record the project's layers, naming and where the data lives in the project memory with `update_memory`, so the
  next conversation does not have to rediscover them.
- `/check` compares the project with these standards.
