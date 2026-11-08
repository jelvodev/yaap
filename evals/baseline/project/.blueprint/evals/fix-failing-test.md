---
summary: "Baseline: find why the test suite fails and fix the cause, not the test"
start:
  commit: 8df907f8dbb3773ada767d045ffa3ef14270ec7e
allowed_tools: [list_files, read_file, search, edit_file, write_file, run_command]
budget:
  steps: 30 steps
  tokens: 800k tokens
  time: 10 min
checks:
  - kind: command
    name: the tests pass
    run: python3 -m unittest discover -s tests -t .
    timeout: 5 min
  - kind: command
    name: the tests are unchanged
    run: git diff --exit-code HEAD -- tests
    timeout: 1 min
  - kind: file
    name: times keep their offset
    path: stockroom/storage.py
    not_contains: strftime("%Y-%m-%dT%H:%M:%S")
---

The test suite fails since the last commit. Find the cause and fix it without changing the tests.
