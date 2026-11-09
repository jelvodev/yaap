---
name: review-code
description: Review a change, a diff, a branch or a pull request through five lenses (correctness, security,
  performance, tests, readability), with a severity (blocking, should, nit) and a confidence for every finding, and
  each finding checked against the code before it is reported. Use this whenever you are asked to review code, before
  a merge or a pull request, and at the end of a plan, because a review that reports guesses wastes the author's
  time and one that misses a real bug costs more.
license: MIT
metadata:
  sources: wshobson/plugins/developer-essentials/skills/code-review-excellence,
    superpowers/skills/requesting-code-review, agency-agents/engineering/engineering-code-reviewer.md, own
---

# Review code

A review finds what would hurt the people who use or maintain the code, and says it so the author can act on it. It
does not rewrite the code to taste or repeat what a linter already reports.

## 1. Know what the change is for

- Read the intent first: the request, the plan and its acceptance criteria, or the pull request's description.
  Without it you can judge style but not correctness.
- Get the diff: `git_diff` for uncommitted changes, or the branch against its base. Note its size. Over about 400
  changed lines, review it in parts (per module or per commit) and say so; large diffs hide bugs.
- Load the project's standards with `load_standard`, the `code-review` engineering standard first when the project
  has it. A finding against a project standard names that standard.
- Check what already ran: tests, lint, type checks. Do not report what they report.

## 2. Read through each lens

Read the whole change once for its shape (does the approach fit the problem, does it fit the project's patterns, is
there a simpler way), then once per lens.

**Correctness.** Does it do what the intent says, for every input? Empty, missing and duplicate values, boundaries
and off-by-one, time zones, concurrent calls, errors from what it calls, partial failure halfway through a change.

**Security.** Input from users, files, the network or a model is checked at the boundary. Queries and commands are
built with parameters, not string joins. Permission checks on every path, not just the obvious one. No secrets in
code, logs, errors or test data. Paths cannot leave their folder.

**Performance.** Queries in a loop, work repeated per item that could happen once, unbounded lists or reads, blocking
calls in async code, missing indexes for new queries. Only where it matters at the expected size; say the size.

**Tests.** Each new behaviour and each fixed bug has a test that would fail without the change. Tests assert on
results, not on mocks. Names say the behaviour. No fixed sleeps. The unwanted cases from the acceptance criteria are
covered.

**Readability.** Names say what things are. A function does one thing. Comments explain why, not what. No dead code,
no copy of a helper that already exists (search for it). Errors say what went wrong and what to do.

## 3. Check every finding before you report it

A finding you did not check is a guess. Before it goes in the report:

- Read the code around it, not only the diff lines. The guard you miss may sit in the caller.
- Follow the data: `find_references` and `find_definition` show who calls it and with what.
- When it is cheap, prove it: a small test, a command, a value traced by hand.
- Drop what does not survive. Lower the confidence of what you could not settle, and say what would settle it.

Confidence per finding:

- **high:** you saw it happen or traced it in the code with no gap;
- **medium:** the code reads that way, but something you could not check could prevent it;
- **low:** a risk worth a look, not shown.

## 4. Grade by effect

- **blocking:** wrong results, lost or corrupted data, a security hole, a broken contract or build, a missing test
  for the change's main behaviour. Must be fixed before merge.
- **should:** a real problem with a smaller effect: an unhandled edge case, a confusing name, a missing test for a
  side path, a slow path at realistic size. Fix in this change unless there is a reason not to.
- **nit:** taste and polish. The author may ignore it. Keep nits few.

Grade by what a user would get if it shipped, not by how easy the fix is.

## 5. Write the review

One complete review, not findings in drips. Each finding says where, what, why it matters and what to do; suggest,
do not dictate. Name what is good when it is worth repeating. Ask when the intent is unclear instead of assuming it
is wrong.

## A second view

The author's own review shares the author's blind spots. In YAAP:

- delegate the review to the `reviewer` agent (`delegate` with `agent: reviewer`), so another context reads it;
- and suggest *Review these changes* under Changes in the rail: a model of another vendor reviews the uncommitted
  changes and its findings show beside the diff. A goal that changed files gets this review by itself.

Treat findings from either like any review: check them with the `receive-review` skill before acting.

## Output template

```
## Review: <what was reviewed>

Intent: <one line>
Size: <files, lines>; ran: <tests, lint, or "nothing">
Verdict: <approve | approve after the "should" findings | changes needed>

### Findings
1. [blocking, high] `path/file.py:42` <what is wrong>.
   Why: <the effect>. Checked: <how you confirmed it>.
   Suggest: <what to do>.
2. [should, medium] `path/other.ts:17` <...>
   Why: <...>. Not checked: <what would settle it>.
3. [nit, high] `path/file.py:88` <...>

### Good
- <what is worth keeping, briefly>

### Questions
- <where the intent is unclear>
```
