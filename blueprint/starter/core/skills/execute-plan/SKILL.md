---
name: execute-plan
description: Carry out an approved implementation plan step by step, with tests first, a check and a progress
  note after every step, and a review of the whole change at the end. Works in goal mode (/goal) and can hand steps
  to workers with a stronger model reviewing them. Use this whenever there is a written plan to build from, or when
  the user says to go ahead with one, so steps are not skipped, merged or forgotten after a compaction.
license: MIT
metadata:
  sources: superpowers/skills/executing-plans, superpowers/skills/subagent-driven-development, own
---

# Execute a plan

Building from a plan is mostly discipline: the thinking was done when the plan was written. Follow it to the letter,
prove every step before the next, and write down as you go what you did, because your own context will not keep it.

## Before step 1

- Read the whole plan once, and the design it names. Note the acceptance criteria and the risks.
- Work on a branch, not on the main branch, unless the user said otherwise. Check with `git_status`.
- Run the test suite once and keep the result: you need to know what was already failing before you started.
- Load the `test-driven-development` skill with `load_skill`; it governs every step.
- Look for progress notes in the plan. A ticked step is finished, whether or not you recall doing it; the plan
  file, `git_status` and the log are the truth after a compaction. Resume at the first open step.
- See what the plan says about commits. Commit when the user asks, or per step when the user approved a plan that
  says so; otherwise the steps stay uncommitted until the user asks.

## Each step

1. **Read the step again**, from the plan file, not from memory. Memory holds a summary; the plan holds the exact
   values.
2. **Red:** write the step's test and run it. It fails, for the reason the step expects.
3. **Green:** make the change. Run the step's check and read its output.
4. **Compare with the plan.** Three outcomes:
   - It matches: go on.
   - The code is wrong: debug it with the `systematic-debugging` skill. Never bend the check to fit.
   - The plan is wrong (a step contradicts the design, an interface does not fit, a command cannot work): choose the
     smallest change that still meets the design, and record it as a decision (below).
5. **Close the step:** run the tests around the change. When the approved plan says to commit per step, commit it
   on the branch with `git_commit`, one step per commit. YAAP checkpoints every change by itself, so the user can
   roll a step back with *Restore to before this*; you take back your own step with git through `run_command`.
6. **Progress note:** tick the step in the plan and add one line under it: the commit (or "not committed"), the
   check and its result, and any decision. Write it in the same turn as the step; it is how you find your place
   after a compaction.

The approval of the plan covers all its steps, so carry on from one to the next without checking in. Pause and ask
the user only when:

- the next action cannot be taken back or reaches beyond the branch (a merge, a push to a shared branch, a migration
  on real data, deleting data);
- it touches secrets or permissions;
- the plan has failed so badly that you could only go on by inventing it anew.

Every departure from the plan gets a line in its progress note: `Decision: <what you chose>. Reason: <why>. Risk:
<what it costs if it is the wrong call>`. The user reads the plan, not your context; a change that is not written
there is one they cannot check.

## In goal mode

`/goal Carry out docs/plans/<topic>.md` runs this skill turn after turn without waiting for the user. The progress
notes in the plan are what keep a long goal on track. End the goal with `finish_goal`:

- `done` once the final review has no blocking findings left and its fixes are made (and committed, when commits
  were agreed); the summary lists what was built, the decisions and what is not verified;
- `blocked` when one of the reasons to pause comes up, with exactly what you need from the user.

A goal that changed files gets a review by a model of another vendor by itself when it ends.

## With workers (optional)

For a plan with many well-specified steps, hand steps to workers and keep the planning and judgement yourself.

- **Delegate one step at a time** with `delegate`: the worker model by default, which is cheaper. It does not see
  this conversation, so give it the step's text, the files, the interfaces from earlier steps, the project's
  constraints and the exact check to run. Never run two workers on the same files at once.
- **Review with a stronger model.** After the worker reports, review its diff with `git_diff` yourself, or delegate
  the review to the `reviewer` agent (`agent: reviewer`), which reads with a stronger preset. The review checks the
  step against the plan and the `review-code` lenses.
- **Trust the diff, not the report.** Run the step's check yourself before you tick the step; a worker saying
  "done" is a claim, not evidence.
- **At most three fix rounds per step.** Send blocking findings back to the worker with the finding and the failing
  check. After three rounds that did not settle it, stop the loop: the problem is in the plan or in your
  understanding, not in one more try. Take the step over yourself with a fresh look, or report it to the user with
  what was tried.

## The final review

When every step is ticked:

1. Run the whole test suite, lint and build, and compare with the result from before step 1.
2. Check each acceptance criterion against the code and a test that proves it (the `verify-before-done` skill).
3. Review the whole diff of the branch with the `review-code` skill, best by the `reviewer` agent so another context
   reads it. Suggest *Review these changes* to the user for a second view by a model of another vendor.
4. Fix the blocking findings in one pass, each with a test that failed first. Note "should" and "nit" findings
   instead of fixing them unasked.
5. Finish with the `finish-branch` skill.

## Report

```
Plan: <path>
Steps: <n of n done>, commits <first>..<last> | not committed
Tests: <command> -> <result> (before: <result>)
Criteria: <n of n proven>; <any not proven, and why>
Decisions:
- <step>: <decision>; risk: <cost if wrong>
Review: <verdict>; open findings: <list or "none">
Not verified: <what could not be checked, or "nothing">
```
