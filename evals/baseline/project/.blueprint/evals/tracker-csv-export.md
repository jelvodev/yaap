---
summary: "Baseline: fix a bug that is only described in the issue tracker (MCP server tracker)"
start:
  commit: 8916086225c95afd2d35e7e00a5614ee4099cc15
allowed_tools:
  - list_files
  - read_file
  - search
  - edit_file
  - write_file
  - run_command
  - tracker__get_issue
  - tracker__search_issues
  - tracker__list_comments
  - tracker__list_issues
approve:
  tools: [edit_file, write_file]
  commands: ["python3 -m unittest"]
budget:
  steps: 40 steps
  tokens: 1M tokens
  time: 15 min
checks:
  - kind: command
    name: every exported row has eight columns
    run: >-
      python3 -c "import csv, io, subprocess, sys;
      out = subprocess.run([sys.executable, '-m', 'stockroom.cli', 'export'], capture_output=True, text=True).stdout;
      rows = list(csv.reader(io.StringIO(out)));
      ok = len(rows) == 73 and all(len(row) == 8 for row in rows) and rows[0][0] == 'sku';
      ok = ok and any(row[1] == 'Screws, wood, 4x40 mm (box of 200)' and row[6] == '47.63' for row in rows);
      sys.exit(0 if ok else 1)"
    timeout: 1 min
  - kind: command
    name: the tests pass
    run: python3 -m unittest discover -s tests -t .
    timeout: 5 min
  - kind: rubric
    name: follows the issue
    rubric: >-
      The reply refers to issue SR-12. The export now quotes fields properly (for example with the csv module),
      keeps the header and the amounts with a dot as the comments on the issue ask, and a test covers a name with a
      comma.
    files: [stockroom/report.py]
---

Fix issue SR-12 from the issue tracker (the `tracker` MCP server) and add a test for it.
