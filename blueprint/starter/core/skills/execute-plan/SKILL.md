---
name: execute-plan
description: Carry out an approved implementation plan step by step, with tests first, a checkpoint and a progress
  note after every step, and a review of the whole change at the end. Works in goal mode (/goal) and can hand steps
  to workers with a stronger model reviewing them. Use this whenever there is a written plan to build from, or when
  the user says to go ahead with one, so steps are not skipped, merged or forgotten after a compaction.
license: MIT
metadata:
  sources: superpowers/skills/executing-plans, superpowers/skills/subagent-driven-development, own
---

# Execute a plan

The plan already holds the decisions. Your job is to carry it out exactly, prove each step, and leave a record that
survives your own forgetting.

## Before step 1

- Read the whole plan once, and the design it names. Note the acceptance criteria and the risks.
- Work on a branch, not on the main branch, unless the user said otherwise. Check with `git_status`.
- Run the test suite once and keep the result: you need to know what was already failing before you started.
- Load the `test-driven-development` skill with `load_skill`; it governs every step.
- Look for progress notes in the plan (ticked steps). Steps already ticked are done: their commits exist even when
  you do not remember them. Check `git_status` and the last commits, and resume at the first open step.

## Each step

1. **Read the step again**, from the plan file, not from memory. Memory holds a summary; the plan holds the exact
   values.
2. **Red:** write the step's test and run it. It fails, for the reason the step expects.
3. **Green:** make the change. Run the step's check and read its output.
4. **Compare with the plan.** Three outcomes:
   - It matches: go on.
   - The code is wrong: debug it with the `systematic-debugging` skill. Never bend the check to fit.
   - The plan is wrong (a step contradicts the design, an interface does not fit, a command cannot work): choose the
     smallest change that still meets the design, and write it down as a ruling (below).
5. **Checkpoint:** run the tests around the change, then commit the step on the branch with `git_commit`, one step
   per commit. YAAP also snapshots the files before every change, so *Restore to before this* on a step's first
   change rolls the whole step back.
6. **Progress note:** tick the step in the plan and add one line under it: the commit, the check and its result, and
   any ruling. Do this in the same turn as the commit. After a compaction this line is how you know where you are.

Do not ask "shall I continue?" between steps; the user approved the plan. Stop and ask only for:

- an action that cannot be undone or reaches outside the branch (a merge, a push to a shared branch, a migration on
  real data, deleting data);
- something security-sensitive (secrets, permissions);
- a plan so broken that every way forward is a guess.

A ruling looks like this, in the progress note: `Ruling: <what you decided>, because <why>; if wrong: <what it
costs>`. A deviation without a ruling is a decision the user never sees.

## In goal mode

`/goal Carry out docs/plans/<topic>.md` runs this skill turn after turn without waiting for the user. The progress
notes in the plan are what keep a long goal on track. End the goal with `finish_goal`:

- `done` after the final review is clean and its fixes are committed, with a summary of what was built, the rulings
  and what is not verified;
- `blocked` when one of the stop reasons above comes up, saying exactly what you need.

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
Steps: <n of n done>, commits <first>..<last>
Tests: <command> -> <result> (before: <result>)
Criteria: <n of n proven>; <any not proven, and why>
Rulings:
- <step>: <ruling>, if wrong: <cost>
Review: <verdict>; open findings: <list or "none">
Not verified: <what could not be checked, or "nothing">
```
