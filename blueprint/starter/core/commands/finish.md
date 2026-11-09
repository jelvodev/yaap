---
sources: [superpowers/skills/finishing-a-development-branch]
description: Finish the work on this branch by running the tests, then merging, opening a pull request or keeping it.
---

Use the `finish-branch` skill (load it with `load_skill`) for the current branch. Notes from the user, if any:

$ARGUMENTS

Run the tests first and show the command and its result; if they fail, stop and report. Then offer the options from
the skill and wait for the user's choice. Merge, push, open a pull request or discard nothing without that choice,
and discard only after the user confirms it a second time.

End with what was done and where: the branch, the commit and the pull request's link if there is one.
