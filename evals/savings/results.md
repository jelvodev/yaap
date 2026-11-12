# Context savings results

Two cases on Freight, each played three times with *Context savings* off and three times on, one play at a time.

- Connection: Claude Code with a subscription. Preset: Sonnet 5.5 as the main model (medium effort), Haiku 5.5 as
  worker and utility model. The runner has pytest.
- Tokens are all tokens of a play, cache included. API calls are the model calls Claude Code made. Cost is the
  catalog price of those tokens (the subscription reports none).
- A play that ended on a network error was played again; those errors are not counted.

| Case | Savings | Passed (of 3) | Median API calls | Median tokens | Median cost (USD) |
|---|---|---|---|---|---|
| fix-failing-quotes | off | 3 | 5 | 92,703 | 0.101 |
| | on | 3 | 6 | 116,406 (+26 %) | 0.102 (+1 %) |
| adopt-carrier-surcharges | off | 3 | 10 | 301,464 | 0.250 |
| | on | 3 | 9 | 247,684 (−18 %) | 0.208 (−17 %) |

How often a saving acted, over the three plays with savings on:

| Saving | fix-failing-quotes | adopt-carrier-surcharges |
|---|---|---|
| Repeated read of an unchanged file | 0 | 2 (about 1,000 characters each) |
| Shortened test output | 0 | 1 |

- The model piped every test run into `tail`, so the output stayed below the size at which the test-output saving
  acts, in both cases and both variants.
- The one shortened test output came from a command that ran the tests and then printed two files; the shortening
  kept only the test summary, and the model ran the file listings again.
- The token differences follow the number of API calls (tokens per call are the same with and without savings), so
  they are variance between plays, not the effect of the savings.

Conclusion: with Claude Code as the connection, context savings change almost nothing on these cases. The setting
stays off by default.
