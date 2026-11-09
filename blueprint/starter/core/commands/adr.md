---
sources: [wshobson/plugins/documentation-generation/skills/architecture-decision-records]
description: Write an architecture decision record with context, options, decision and consequences.
---

Use the `architecture-decision` skill (load it with `load_skill`) to record this decision:

$ARGUMENTS

Look for the project's existing records first (often `docs/adr/` or `docs/decisions/`) and follow their numbering and
format. If the decision has not been made yet, lay out the options with their trade-offs and ask; do not decide for
the user. Write the record as the next number, with status Proposed unless the user says it is accepted.

End with the file's path and the decision in one sentence.
