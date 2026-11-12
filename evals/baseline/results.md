# Baseline results

The baseline set is six cases on Stockroom: goal-csv-import, fix-failing-test, fix-vat-rounding, move-discount-rules,
find-unused-functions and tracker-csv-export. Each was played three times on YAAP 0.6.2, one play at a time.

- Connection: Claude Code with a subscription (no per-token price is reported, so cost is not recorded).
- Preset: Sonnet 5.5 as the main model (medium effort), Haiku 5.5 as worker and utility model.

| Case | Passed (of 3) | Median tokens | Median steps |
|---|---|---|---|
| goal-csv-import | 3 | 142,247 | 20 |
| fix-failing-test | 3 | 126,090 | 8 |
| fix-vat-rounding | 3 | 116,213 | 10 |
| move-discount-rules | 3 | 149,164 | 19 |
| find-unused-functions | 2 | 104,537 | 6 |
| tracker-csv-export | 2 | 119,115 | 12 |

Of the 18 plays, 16 passed. The first measurement of goal-csv-import and tracker-csv-export exposed two faults (the answer
check's reply limit and refused edits after reading from the tracker); both were fixed and the two cases were played again
on the fixed code (the other four on 0.6.2). Tokens are all tokens of a play, cache included.

## Context savings on

Same six cases, three plays each, one at a time, baseline preset, context savings on. "Change" is the median tokens of
a case against the baseline.

| Case | Passed (of 3) | Median tokens | Change | Median steps |
|---|---|---|---|---|
| goal-csv-import | 3 | 188,026 | +32 % | 21 |
| fix-failing-test | 3 | 126,913 | +1 % | 8 |
| fix-vat-rounding | 3 | 123,580 | +6 % | 10 |
| move-discount-rules | 3 | 148,790 | 0 % | 19 |
| find-unused-functions | 2 | 101,762 | -3 % | 5 |
| tracker-csv-export | 2 | 119,256 | 0 % | 11 |

15 of 18 plays passed (baseline 16), no errors. The summed median tokens are 7 % above the baseline, so on these cases
the setting saved nothing. It stays off by default.

## Delegation brief

Same cases and method, baseline preset with delegation set to `brief`, context savings off.

| Case | Passed (of 3) | Median tokens | Change | Median steps |
|---|---|---|---|---|
| goal-csv-import | 2 | 220,759 | +55 % | 21 |
| fix-failing-test | 3 | 124,640 | -1 % | 8 |
| fix-vat-rounding | 3 | 119,267 | +3 % | 9 |
| move-discount-rules | 3 | 148,035 | -1 % | 18 |
| find-unused-functions | 2 | 110,078 | +5 % | 6 |
| tracker-csv-export | 1 | 140,692 | +18 % | 13 |

14 of 18 plays passed (baseline 16), no errors. The summed median tokens are 14 % above the baseline and two cases lost
a pass each. It stays off by default.
