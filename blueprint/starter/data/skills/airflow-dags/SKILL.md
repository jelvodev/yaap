---
name: airflow-dags
description: Write Airflow DAGs whose tasks are idempotent, retry safely, wait without blocking workers and can be
  tested before they are deployed. Use this whenever you create or change an Airflow DAG, task, operator or sensor,
  set a schedule, catchup or retries, debug a failed or stuck DAG run, or plan a backfill in Airflow, even for a small
  change, because a scheduler reruns tasks in ways a script never is.
license: MIT
metadata:
  sources: wshobson/plugins/data-engineering/skills/airflow-dag-patterns
---

# Airflow DAGs

Airflow retries tasks, reruns old intervals and parses DAG files constantly. A DAG is good when all three are
harmless. Check the Airflow version first (`airflow version`, or the project's lock file): imports and defaults
changed between major versions. The examples use Airflow 3; in Airflow 2 the decorators come from
`airflow.decorators`.

## Tasks work on their interval

Each run has a data interval (`data_interval_start`, `data_interval_end`). A task reads and writes exactly that
interval and nothing based on the current time. Then a retry, a manual rerun or a backfill of last March does the
right thing.

- Write with a window replace or a merge on a key (see the `pipeline-design` skill), never a plain append.
- Write files to a path that contains the interval, and overwrite it on a rerun.
- Make a task all or nothing: write to a temporary table or path, then swap or rename.

## Keep the DAG file light

The scheduler imports every DAG file often. Code at the top level of the file runs every time.

- No database queries, API calls or heavy imports at the top level. Do them inside tasks.
- Keep the logic in a normal module that the DAG imports, so it can be tested without Airflow.
- A static `start_date` (never `datetime.now()`), with a time zone.

## A DAG in this shape

```python
from datetime import timedelta

import pendulum
from airflow.sdk import dag, task

from pipelines import orders


@dag(
    schedule="@daily",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(hours=1),
    },
    tags=["orders"],
)
def orders_daily():
    @task
    def extract(data_interval_start=None, data_interval_end=None) -> str:
        # Writes one file per interval; a rerun overwrites the same path.
        return orders.extract(data_interval_start, data_interval_end)

    @task
    def load(path: str, data_interval_start=None, data_interval_end=None) -> None:
        orders.replace_window(path, data_interval_start, data_interval_end)

    load(extract())


orders_daily()
```

Only the file path travels between the tasks. XCom is for small values; large data goes to storage and the task
passes its location.

## Retries and timeouts

- Retry transient failures (network, a busy warehouse) with a delay; exponential backoff where the version
  supports it. A retry of a non-idempotent task makes things worse, so fix the task first.
- Fail fast on errors a retry cannot fix (bad credentials, a schema mismatch): raise them so the run stops.
- Every task has an `execution_timeout`, so a hung task does not hold a worker for days.

## Waiting for data

A sensor waits for a file, a partition or another DAG. A sensor in the default mode holds a worker slot while it
waits. Use `mode="reschedule"` or a deferrable sensor, and always a `timeout`, so a source that never arrives fails
the run instead of blocking it.

Prefer a data-aware schedule (assets or datasets, depending on the version) over a sensor when the upstream is also
an Airflow DAG.

## Catchup and backfills

- `catchup=False` unless you really want every missed interval run when the DAG is switched on.
- `max_active_runs=1` when runs must not overlap, for example when each one merges into the same table.
- Backfill with the scheduler's backfill feature over the same DAG; never with a separate script.

## Secrets and connections

Credentials live in Airflow connections or a secrets backend, never in the DAG file or a variable that is printed.
Tasks refer to a connection id.

## Test before deploying

1. The DAG imports without errors: a test that loads the DAG folder and asserts there are no import errors, or
   `airflow dags list-import-errors`.
2. The task logic has unit tests in the module, without Airflow.
3. One run of the DAG for a single date in a development environment: `airflow dags test <dag_id> <date>`. Run it a
   second time for the same date and check the table holds the same rows.

## In YAAP

- Run the tests and the single-date run with `run_command` and quote the result.
- When the DAG cannot run here (no Airflow, no access to the sources), say so and give the user the commands to run.
- Record the Airflow version and the project's conventions in the project memory with `update_memory`.

## Report

```
DAG: <id, schedule, catchup, what changed>
Idempotence: <how each task replaces its interval; result of the second run>
Tests: <import test, unit tests, the single-date run, with results>
Not verified: <for example the real sources, or the production pool sizes>
```
