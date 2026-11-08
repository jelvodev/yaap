---
summary: "Baseline: search the whole Python codebase for functions nothing uses"
start:
  commit: 8916086225c95afd2d35e7e00a5614ee4099cc15
allowed_tools: [list_files, read_file, search, run_command]
budget:
  steps: 40 steps
  tokens: 800k tokens
  time: 10 min
checks:
  - kind: command
    name: nothing changed
    run: git diff --exit-code HEAD
    timeout: 1 min
  - kind: rubric
    name: finds exactly the three
    rubric: >-
      The reply lists exactly these three functions as unused, each with its file: Inventory.legacy_bulk_adjust in
      stockroom/inventory.py, cents_to_words in stockroom/pricing.py and format_legacy_row in stockroom/report.py.
      It does not list functions that are used by other code or by the tests (such as restock_plan, turnover,
      kit_available or short_name). Line numbers, when given, are right. It changes no files.
---

Which functions and methods in the `stockroom/` package are not used anywhere else in the project, neither by other
code nor by the tests? List each with its file and line. Do not change anything.
