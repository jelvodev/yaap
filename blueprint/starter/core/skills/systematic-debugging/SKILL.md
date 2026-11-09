---
name: systematic-debugging
description: Find the root cause of a bug, test failure, crash or unexpected behaviour before changing any code. Use
  this whenever something does not work as expected, a test fails, an error appears, or an earlier fix did not hold,
  even when the cause looks obvious, because the obvious cause is often a symptom.
license: MIT
metadata:
  sources: superpowers/skills/systematic-debugging, wshobson/plugins/developer-essentials/skills/debugging-strategies
---

# Systematic debugging

Fixing a symptom makes the bug come back somewhere else. Find the cause first, then fix it once.

## 1. Reproduce

Make the failure happen on demand: the failing test, the command, the request, the steps in the browser. Write down
the exact command and the exact output. If it cannot be reproduced, collect what is known (logs, the input, the
version, when it started) and say so; do not guess at a fix.

If there is no test that shows the bug, write one now. It fails for the right reason before the fix and passes after
it, and it keeps the bug from returning.

## 2. Read the evidence

Read the whole error and the whole stack trace, from the bottom (where it started) to the top. Read the code at each
line it names. Check what changed recently: `git_diff`, the last commits, a dependency update.

## 3. Narrow it down

- Find the last place where the data is right and the first place where it is wrong. The cause is between them.
- Halve the search: comment out, bisect commits, or shrink the input until the failure only just remains.
- Compare with something that works: a passing test, an older version, a similar function.
- Add temporary logging at the boundaries rather than guessing. Remove it afterwards.

## 4. One hypothesis at a time

State the hypothesis in one sentence ("the date is parsed in local time, so it is off by a day after 23:00"). Test it
with the smallest experiment that could prove it wrong. Change one thing at a time; changing three at once tells you
nothing.

## 5. Fix the cause

Fix it where it starts, not where it shows. Then run the reproducing test, the tests around it and the build, and
read their output.

## When three fixes failed

After three attempts that did not hold, stop fixing. The model of how the code works is wrong. Go back to step 2,
list what you assumed, and check each assumption. Tell the user what you tried and what you learned; a fresh view
(another agent, the user) often sees it at once.

## In YAAP

- YAAP checkpoints every change by itself. Take back a failed experiment with git (`git restore <file>` through
  `run_command`), or ask the user to use *Restore to before this*.
- Hand a wide search ("where is this value set?") to an exploring worker with `delegate`, so the conversation stays
  small.
- For a web app, run it with `start_process` and look at it in the preview or with `open_browser`.

## Report

End with this shape:

```
Cause: <one sentence>
Evidence: <the command or test, and what it showed before and after>
Fix: <what changed, file and line>
Not verified: <what could not be checked, or "nothing">
```
