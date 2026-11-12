---
summary: "Savings: six rule changes in a long module, then a review that reads the unchanged notice again"
start:
  commit: 71ec56b286525cf781c9ac7cce548c98dbe1362d
allowed_tools: [list_files, read_file, search, edit_file, write_file, run_command]
budget:
  steps: 40 steps
  tokens: 1200k tokens
  time: 15 min
checks:
  - kind: command
    name: the tests pass
    run: python3 -m pytest -q -p no:cacheprovider
    timeout: 5 min
  - kind: command
    name: quotes follow the notice
    run: >-
      python3 -c "from freight.rates import *;
      box = Parcel(1.0, 20, 15, 10);
      lines = lambda *a: {line.label: line.cents for line in quote(*a).lines};
      assert lines(box, Destination('NL', '1012 AB')) == {'Base rate': 695, 'Fuel': 97};
      assert lines(box, Destination('NL', '9166 AB'))['Remote area'] == 495;
      assert lines(box, Destination('NL', '1791 AB'))['Remote area'] == 495;
      assert 'Remote area' not in lines(box, Destination('NL', '9171 AA'));
      assert lines(Parcel(1.0, 101, 10, 10), Destination('NL'))['Oversize'] == 1500;
      assert 'Oversize' not in lines(Parcel(1.0, 100, 30, 30), Destination('NL'));
      assert lines(box, Destination('DE'), Options(saturday=True))['Saturday delivery'] == 900;
      assert lines(box, Destination('NL'), Options(saturday=True))['Saturday delivery'] == 600;
      assert lines(box, Destination('NL'), Options(signature=True))['Signature'] == 150;
      insured = Parcel(1.0, 20, 15, 10, declared_value_cents=15000);
      assert lines(insured, Destination('NL'), Options(signature=True)).get('Signature', 0) == 0;
      assert lines(insured, Destination('NL'))['Insurance'] == 300;
      assert lines(Parcel(1.0, 20, 15, 10, declared_value_cents=40050), Destination('NL'))['Insurance'] == 401;
      assert 'Insurance' not in lines(Parcel(1.0, 20, 15, 10, declared_value_cents=10000), Destination('NL'))"
    timeout: 1 min
  - kind: command
    name: other zones still refuse Saturday delivery
    run: >-
      python3 -c "import unittest; from freight.rates import *;
      refused = unittest.TestCase().assertRaises;
      refused(NotAccepted, quote, Parcel(1.0, 20, 15, 10), Destination('FR'), Options(saturday=True));
      refused(NotAccepted, quote, Parcel(1.0, 20, 15, 10), Destination('US'), Options(saturday=True))"
    timeout: 1 min
  - kind: file
    name: the rules name the new surcharges
    path: docs/rules.md
    contains: ["14 %", "4.95", "9166", "100 cm", "9.00", "3.00"]
  - kind: file
    name: the tests cover Schiermonnikoog
    path: tests/test_surcharges.py
    contains: "9166"
---

Our carrier announced new surcharges in `docs/carrier-notice.md`, and we adopt them now. Do this in two steps in
this reply:

1. Change `freight/rates.py`, section 4 of `docs/rules.md` and the tests so that they follow every point of the
   notice. Keep everything the notice does not name as it is.
2. Then check your work before you answer: read `docs/carrier-notice.md` again and compare each point with
   `freight/rates.py` and `docs/rules.md`, and run the tests.

In your answer, list each point of the notice with the function that implements it.
