---
summary: "Baseline: a small feature across the report module, the CLI and the tests"
start:
  commit: 8916086225c95afd2d35e7e00a5614ee4099cc15
allowed_tools: [list_files, read_file, search, edit_file, write_file, run_command]
budget:
  steps: 40 steps
  tokens: 1M tokens
  time: 15 min
checks:
  - kind: command
    name: the command lists the low items, most missing first
    run: >-
      python3 -c "import subprocess, sys;
      out = subprocess.run([sys.executable, '-m', 'stockroom.cli', 'low-stock'], capture_output=True, text=True).stdout;
      ok = 'SR-1053' in out and 'SR-1046' in out and 'SR-1001' not in out and out.find('SR-1053') < out.find('SR-1046');
      sys.exit(0 if ok else 1)"
    timeout: 1 min
  - kind: command
    name: the tests pass
    run: python3 -m unittest discover -s tests -t .
    timeout: 5 min
  - kind: file
    name: the CLI has the command
    path: stockroom/cli.py
    contains: low-stock
  - kind: rubric
    name: tested and consistent
    rubric: >-
      The change adds a low-stock command to the CLI that lists every item whose available units are at or below
      its reorder level, with how many are missing, most missing first; it reuses Inventory.needs_reorder rather than
      repeating its rule, follows the style of the other reports, and adds tests for the new report and command.
    files: [stockroom/cli.py, stockroom/report.py]
---

Add a `low-stock` command to the CLI (`python3 -m stockroom.cli low-stock`): it lists every item whose available
units are at or below its reorder level, with its available units, its reorder level and how many are missing,
the item with the most missing first. Add tests.
