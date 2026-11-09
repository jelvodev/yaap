---
name: verify-before-done
description: Prove a change works before calling it done, with fresh output of the command that shows it and each
  claim beside its evidence. Use it before saying a task is finished, a bug fixed or tests pass, before a commit, pull
  request or finish_goal, and when a worker reports success.
license: MIT
metadata:
  sources: superpowers/skills/verification-before-completion, agency-agents/testing/testing-reality-checker.md, own
---

# Verify before done

Evidence first, then the claim. "Done" means you ran the check that proves it in this reply and read what it
printed. Until then the status is "not verified", whatever you expect.

## The check, for every claim

1. **Name the claim:** what exactly are you about to say? "The tests pass", "the bug is fixed", "the page shows the
   new total".
2. **Find the command that proves it.** Not one that is near it: a lint run does not prove a build, a unit test does
   not prove the page renders.
3. **Run it now, in full.** Not an earlier run, not part of the suite. The code changed since then.
4. **Read the output:** the exit code, the number passed and failed, the warnings, the last lines.
5. **Say what it shows.** If it confirms the claim, state the claim with the command and the result. If it does not,
   state the real status. If you could not run it, say that.

## What proves what

| Claim | Evidence that counts | Not enough |
|---|---|---|
| Tests pass | The suite's output, with 0 failed | An earlier run, one test file, "should pass" |
| The build works | The build command exits 0 | Lint passing, the editor shows no errors |
| A bug is fixed | The reproduction (a test or steps) failed before and passes now | The code changed, so it is fixed |
| A regression test works | It fails with the fix taken out and passes with it | It passes once |
| The page works | The preview or `open_browser`, the flow clicked through, the console read | The component compiles |
| A worker finished | `git_diff` shows the change, and you ran its check yourself | The worker's report |
| The task is complete | Each acceptance criterion checked one by one | The tests pass |

## Words that give a guess away

"Should", "probably", "seems to", "looks right", "I'm confident", and "Done!" before any output. When you notice
one, run the check instead of writing the sentence.

## Be the sceptic

- Assume not ready until the evidence says otherwise. A first version usually needs another round.
- A report of zero problems, from a worker or a tool, is a reason to look once more, not to stop looking.
- Check the requirement, not only the tests: re-read the request or the plan's acceptance criteria and tick each
  one with the evidence for it. Tests can pass while a criterion is missing.
- Look at what the user will see. For a web app, run it with `start_process` and its port and look at it in the
  preview beside the chat; for a CLI, run it the way the user will.

## When something cannot be checked

No test exists, it needs credentials you do not have, it runs only in production, it needs a device. Say so in the
report, plainly, and say what the user can run to check it. An honest "not verified" is worth more than an implied
"works".

## In YAAP

- Run checks with `run_command` and quote the command and the relevant lines of its output.
- `/check` checks the project against its standards; `/eval` runs the project's eval cases when it has them.
- In goal mode, run this check before `finish_goal` with `done`; the summary carries the table below.

## Output template

End the reply with this table, one row per claim:

```
| Claim | Command or check | Result |
|---|---|---|
| Tests pass | `uv run pytest -q` | 214 passed, 0 failed |
| Bug #12 fixed | `uv run pytest tests/test_dates.py -q` | failed before the fix, passes now |
| Page shows the total | Preview, cart with two items | total shows 24.90 |

Not verified: <what could not be checked and how the user can check it, or "nothing">
```
