---
summary: "Baseline: a one-line bug fix in pricing with a regression test"
start:
  commit: 8916086225c95afd2d35e7e00a5614ee4099cc15
allowed_tools: [list_files, read_file, search, edit_file, write_file, run_command]
budget:
  steps: 30 steps
  tokens: 800k tokens
  time: 10 min
checks:
  - kind: command
    name: half a cent rounds up
    run: >-
      python3 -c "from stockroom.pricing import vat_cents;
      assert vat_cents(250, 21) == 53, vat_cents(250, 21);
      assert vat_cents(150, 9) == 14 and vat_cents(50, 9) == 5;
      assert vat_cents(1000, 21) == 210 and vat_cents(1999, 9) == 180"
    timeout: 1 min
  - kind: command
    name: the tests pass
    run: python3 -m unittest discover -s tests -t .
    timeout: 5 min
  - kind: command
    name: a regression test was added
    run: >-
      python3 -c "import pathlib, sys;
      text = pathlib.Path('tests/test_pricing.py').read_text(encoding='utf-8');
      sys.exit(0 if text.count('def test_') >= 5 else 1)"
    timeout: 1 min
---

Our bookkeeper reports that the VAT is sometimes one cent too low: a net amount of EUR 2.50 at 21% gives EUR 0.52 VAT,
but half a cent must round up, so it should be EUR 0.53. Fix it and add a regression test. Keep the other tests
green.
