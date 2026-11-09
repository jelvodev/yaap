---
sources: [wshobson/plugins/developer-essentials/skills/code-review-excellence, agency-agents/engineering/engineering-code-reviewer.md]
category: code-review
summary: Review for what can break, label each finding by how much it matters, and keep changes small enough to
  review well.
rules:
- Label each finding blocking, should or nit, so the author knows what must change before merging and what is taste.
- Look at correctness and security first, then tests, then readability and performance; leave formatting to a linter.
- Make each finding specific, with file and line, what goes wrong, why it matters and a fix.
- Report only what you checked in the code; put what you could not confirm as a question, not as a finding.
- Keep a change to about 200 to 400 lines; past that, reviewers miss things, so split it.
---

# Code review

How to review a change, and how to prepare one for review. The rules above are always in the prompt; this text
explains them. The `review-code` skill has the step-by-step method and the report format.

## Labels

- **blocking:** it is wrong or unsafe and must change before the merge: a bug, a security hole, data loss, a broken
  contract, a missing test for the behaviour that changed.
- **should:** a real improvement that the author should make, but the change is not wrong without it: unclear
  naming, duplicated logic, a missing edge case that is unlikely.
- **nit:** taste and small things. The author may ignore them. If a linter could catch it, configure the linter.

A review without labels makes every comment look equally urgent, so the important one drowns.

## What to check, in order

1. **Correctness.** Does it do what the task asked? Edge cases: empty input, one item, very many, missing values,
   the error path, concurrent calls.
2. **Security.** Input validated where it enters, access checked on the server, no secret in the change, queries and
   commands built with parameters. See the `security` standard.
3. **Tests.** Is the changed behaviour covered by a test that would fail without the change? Do the tests check
   results rather than internals?
4. **Design and readability.** Does it fit how the project already does things? Is there an existing helper it
   should reuse? Would a newcomer follow it?
5. **Performance,** where it matters: a query inside a loop, work repeated on every request, unbounded growth.

## Findings

Each finding names the file and line, says what goes wrong in a concrete case ("an empty list makes this divide by
zero"), why it matters, and what would fix it. "This looks off" is not a finding.

Before you report a finding, check it: read the code it depends on, follow the call, run the test if you can. A
review full of false alarms teaches the author to skip the real ones. When you are not sure, ask a question instead.

Say what is good when it is worth keeping, and say plainly when nothing needs to change.

## Size

A reviewer catches most in a change of a few hundred lines. A larger change gets a skim, so split it: the refactor
first, then the feature on top. Generated files and lock files do not count, but say that they are there.

## Receiving a review

Check each comment against the code before acting on it; a reviewer can be wrong too. Fix what is right, and answer
what is not with the reason. The `receive-review` skill covers this. In YAAP, *Review these changes* under Changes
asks a model of another vendor for a second look at the uncommitted changes.
