---
summary: "Savings: fix three bugs behind a long pytest run with a few failures among many passes"
start:
  commit: dc96632d2f5fd8690f37f260661bd02df64be148
allowed_tools: [list_files, read_file, search, edit_file, write_file, run_command]
budget:
  steps: 30 steps
  tokens: 800k tokens
  time: 10 min
checks:
  - kind: command
    name: the tests pass
    run: python3 -m pytest -q -p no:cacheprovider
    timeout: 5 min
  - kind: command
    name: the tests are unchanged
    run: git diff --exit-code HEAD -- tests pytest.ini
    timeout: 1 min
  - kind: command
    name: bands include their limit, Terschelling is remote and export has no VAT
    run: >-
      python3 -c "from freight.rates import *;
      assert [weight_band(kg) for kg in (2.0, 5.0, 10.0, 20.0, 31.5)] == [0, 1, 2, 3, 4];
      assert remote_area_surcharge(Destination('NL', '8884 AB')) == 450;
      assert vat_cents(1000, 4) == 0 and vat_cents(1000, 3) == 210"
    timeout: 1 min
---

The quote tests fail since the last commit. Run the test suite (`python3 -m pytest`), go through the results and fix
the causes in `freight/`, without changing the tests. Finish with a test run that passes.
