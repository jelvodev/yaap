---
name: dbt-models
description: Lay out, write, test and document dbt models in staging, intermediate and mart layers, with incremental
  models that survive reruns, snapshots for history and tests on every key. Use it whenever you add or change a dbt
  model, source, test, macro or snapshot, or set up dbt in CI.
license: MIT
metadata:
  sources: wshobson/plugins/data-engineering/skills/dbt-transformation-patterns
---

# dbt models

dbt works when every model has one job, a clear name and tests on its keys. Before changing anything, read
`dbt_project.yml`, the folder layout and two or three existing models, and follow what the project already does.

## Layout

```
models/
  staging/<source>/        _<source>__sources.yml, _<source>__models.yml, stg_<source>__<entity>.sql
  intermediate/<area>/     int_<entity>_<verb>.sql
  marts/<area>/            _<area>__models.yml, fct_<event>.sql, dim_<entity>.sql
```

| Layer | Does | Materialised as |
|---|---|---|
| staging | One model per source table: rename, cast, clean values. No joins, no business logic | view |
| intermediate | Joins and logic shared by several marts | view or ephemeral |
| marts | Facts (events, one row per thing that happened) and dimensions (entities) for readers | table or incremental |

Why: a source column is renamed and cast in exactly one place, so a source change touches one model.

## Rules for every model

- Refer to other models with `ref()` and to raw tables with `source()`. A hard-coded table name breaks the lineage,
  the build order and environments.
- One model, one grain. Say it in the model's description ("one row per order line").
- Put repeated SQL in a macro only when it repeats three times; before that, a macro hides more than it saves.
- No `select *` in marts. Name the columns so a source change cannot silently add one.

## Sources and freshness

```yaml
version: 2
sources:
  - name: shop
    schema: raw_shop
    loaded_at_field: _loaded_at
    freshness:
      warn_after: {count: 6, period: hour}
      error_after: {count: 24, period: hour}
    tables:
      - name: orders
      - name: customers
```

Run `dbt source freshness` before the build, so a stale source fails loudly instead of producing old numbers.

## Tests

Every model's key gets `unique` and `not_null`. Foreign keys get `relationships`. Status and type columns get
`accepted_values`. Business rules get a singular test: a SQL file in `tests/` that returns the rows that break the
rule.

```yaml
version: 2
models:
  - name: fct_orders
    description: One row per order, with cancellations applied.
    columns:
      - name: order_id
        description: The shop's order number.
        data_tests:
          - unique
          - not_null
      - name: customer_id
        data_tests:
          - not_null
          - relationships:
              to: ref('dim_customers')
              field: customer_id
      - name: status
        data_tests:
          - accepted_values:
              values: ['paid', 'refunded', 'cancelled']
```

Newer dbt versions prefer the test arguments under an `arguments:` key; follow the version the project uses.

For marts that other teams or tools read, enforce a contract (`config: {contract: {enforced: true}}`). It then
needs every column listed with its `data_type`, and the build fails when the SQL no longer matches.

## Incremental models

Use incremental for large facts where a full rebuild is too slow, not by default.

```sql
{{ config(
    materialized="incremental",
    unique_key="order_id",
    on_schema_change="fail"
) }}

select
    order_id,
    customer_id,
    ordered_at,
    status,
    total_amount,
    updated_at
from {{ ref("stg_shop__orders") }}
{% if is_incremental() %}
where updated_at > (select max(updated_at) from {{ this }}) - interval '3 days'
{% endif %}
```

- `unique_key` makes a rerun update rows instead of duplicating them.
- The lookback re-reads late rows; the date arithmetic differs per warehouse, so use the project's dialect or
  `dbt.dateadd`.
- `on_schema_change="fail"` stops a silent column change; add the column on purpose and run once with
  `--full-refresh`.

## Documentation

Every model and every key column has a description. Mark columns that hold personal data in their description or
with a `meta` tag, so readers and reviewers can see them. Check the lineage with `dbt docs generate`.

## Running and checking

- Build what changed and everything after it: `dbt build --select state:modified+` in CI against a saved manifest,
  or `dbt build --select <model>+` locally.
- Run against a development target, never the production one, unless the user asks.
- `dbt build` runs models and tests in order; read the summary line and every failed test before calling it done.

## In YAAP

- Run dbt with `run_command` and quote the summary line in the reply.
- A new mart is a design choice: agree on its grain and columns with the user before writing it.
- Record the project's naming and targets in the project memory with `update_memory`.

## Report

```
Models: <added or changed, with their layer and materialisation>
Tests: <added; result of dbt build with the counts>
Breaking: <columns or grain changed for readers, or "none">
Not verified: <for example production volumes, or the full refresh>
```
