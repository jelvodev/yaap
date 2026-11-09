---
sources: [superpowers/skills/writing-plans, own]
description: Write a short plan with steps, files and checks for a task, and agree on it before building.
---

Use the `write-plan` skill (load it with `load_skill`) to plan this task:

$ARGUMENTS

Read the code the task touches before you plan, and name real files and functions. Change no code while planning.
If the task is unclear or has two sensible designs, ask one short question first. For a large task, when `delegate`
is available (in a project conversation), the `planner` agent can draft the plan with a stronger model (`delegate`
with `agent: planner`); it returns the plan in its reply, and you save it. End with the plan in the skill's format and
ask whether to go ahead; build nothing until the user agrees.
