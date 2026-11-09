---
sources: [wshobson/plugins/comprehensive-review/commands/full-review.md, own]
description: Review the uncommitted changes for bugs, security problems and missing tests before they are committed.
---

Review the uncommitted changes in this project. What the user wants looked at in particular, if anything:

$ARGUMENTS

1. Look at `git_status` and `git_diff`. If nothing is uncommitted, say so and stop.
2. Hand the review to the `reviewer` agent with `delegate`, passing the focus above and the task the changes were
   for. If there is no `reviewer` agent, load the `review-code` skill with `load_skill` and review the changes
   yourself.
3. Report the findings in the skill's format, blocking first, each with file and line. Change no files; fixing is a
   next step the user chooses.

End with one line saying that this review was made in this conversation, and that *Review these changes* under
Changes gives a second review by a model of another vendor. Do not present this review as that one.
