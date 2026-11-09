---
name: write-skill
description: Write, improve or test a Blueprint skill for YAAP - a SKILL.md with a description that makes the agent
  load it at the right moments, a lean body that explains why, and a check with and without the skill on realistic
  prompts. Use this whenever the user wants to turn a way of working into a skill, create or edit a skill, asks why a
  skill is not used or is used at the wrong time, or wants to measure whether a skill helps, even if they call it a
  prompt, a playbook or a checklist.
license: Apache-2.0
metadata:
  sources: anthropic-skills/skills/skill-creator, superpowers/skills/writing-skills, own
---

# Write a skill

A skill is knowledge the agent loads only when a task needs it. Two things decide whether it helps: the description,
which is all the agent sees until it loads the skill, and the body, which must change what the agent does compared
with not having it. Write both for that, and test both.

## How YAAP uses a skill

A skill is a folder `skills/<name>/` with a `SKILL.md` in a Blueprint layer (global, an environment, or a project's
`.blueprint/`). Every conversation's prompt lists each skill by name with its description. The agent reads that
line, decides whether the task calls for the skill, and loads the body with `load_skill`. A pinned skill (from the
chat input, or a default skill in the settings) is in the prompt for the whole conversation; an agent's own skills
come with that agent.

So a skill the agent never loads does nothing, and a skill it loads for the wrong task costs context and steers
the work wrong.

## 1. Decide what it is for

- **Worth a skill:** a method that is not obvious, that you would want followed the same way each time, and that
  applies across tasks: a debugging method, a review checklist, a release routine, a document format.
- **Not a skill:** a one-off fix (that is a commit message), a project fact (that is the project memory), a rule for
  every reply (that is a standard), or something a linter or test can enforce (automate it).

When the user wants to capture what just happened in a conversation, take the steps, the corrections they made and
the output shape from the conversation, and confirm the gaps with them.

## 2. See the failure first

Before writing, give the agent two or three realistic tasks the skill is meant for, without the skill, and read
what it does. Note what goes wrong: a step it skips, a wrong order, an output in the wrong shape, a rule it argues
its way around. If nothing goes wrong, the skill is not needed. If something does, the skill is written against
exactly those failures, not against imagined ones.

## 3. Write the description

The description is the trigger. It says what the skill does and when to use it, in words the user would actually
type, and it leans towards loading: agents tend to skip skills they would have benefited from.

- Name the situations, the symptoms and the words users use: "a test fails", "flaky", "it worked yesterday".
- Add "even when ..." for the case where the agent would wrongly think it can do without.
- Do not summarise the steps. An agent that reads the steps in the description may follow that summary and never
  load the body.
- At most 1024 characters. In plain YAML, avoid a colon followed by a space inside the text, or quote the whole value.

Weak: "Helps with database migrations."
Better: "Write and check a database migration that keeps existing data. Use this whenever a model, table or column
changes, a migration fails or must be undone, or before deploying a schema change, even a small one."

## 4. Write the body

- **Lean.** Under about 300 lines. Every paragraph must change behaviour; cut what the agent does well anyway.
- **Imperative, with the reason.** "Run the tests before the change, because a red suite hides new failures" works
  better than a capitalised MUST: an agent that knows why applies the rule to cases you did not foresee.
- **Steps in order**, each with how to tell it is done.
- **An output template** when the result has a shape: a report, a document, a record. A slot in a template is
  followed more reliably than a sentence asking for it.
- **Fit the form to the failure.** A rule the agent skipped under pressure needs a clear rule and the excuse it will
  try, answered. An output in the wrong shape needs a positive template, not a list of don'ts. Behaviour that
  depends on a condition needs the condition stated ("if the project has a CHANGELOG, follow its format").
- **One good example** beats several, and it must be correct.
- **YAAP's tools by name** where they help: `delegate` for wide searches, `start_process` and the
  preview, `update_memory`, `open_document`.
- **One file.** `load_skill` returns `SKILL.md` only, so everything the skill needs goes in it. When real variants
  (per cloud, per framework) make it too long, write a skill per variant instead, each with its own trigger.

Front matter: `name` (equals the folder: lowercase letters, digits and hyphens), `description`, `license`, and
`metadata.sources` when the method comes from somewhere. Markdown only; a skill with scripts or binaries is refused
when installed from a package.

## 5. Test with and without

Run the same two or three tasks from step 2 again, now with the skill, each in a fresh conversation. Compare the
two runs: did the failures go away, did new ones appear, did it take more steps or tokens than it saved? Read what
the agent did, not only its answer; *Show trace* lists every step.

To measure instead of eyeball, make the tasks eval cases: *Save as eval case* on a conversation that went well, or a
file in `.blueprint/evals/`. Then put the skill on a branch of its Blueprint layer and use **Compare** in the Evals
panel with that layer at the branch as the variant: YAAP plays the cases with and without the skill, three times
each, and shows the pass rates and costs side by side. `/eval` runs the cases from the chat.

## 6. Tune the trigger

Write about ten requests that should load the skill and about ten that should not. The ones that should not are the
useful part: near misses that share words with the skill but need something else ("write a migration guide for
users" against a database migration skill). Make them realistic, with file names, context and casual wording; very
short one-step requests rarely load any skill, so they test nothing.

Send each in a fresh conversation and check in the trace whether `load_skill` was called. Change the description
where it misses or fires wrongly, and run the set again. Keep the set: it is the regression test for the next edit.

## 7. Keep it alive

Rerun the tests after each edit to the skill. *Learn from this* in YAAP can propose a missing skill step after a
conversation that went wrong; measure such a proposal before accepting it.

## Checklist

```
- [ ] Baseline run without the skill showed a real failure
- [ ] Description says what and when, in users' words, without the steps; at most 1024 characters
- [ ] Body under about 300 lines; each rule says why; output template where the result has a shape
- [ ] Runs with the skill fixed the baseline failures without new ones
- [ ] Trigger set: should-load and near-miss requests checked in the trace
- [ ] Front matter: name equals folder, license, metadata.sources
```

Adapted from the anthropics/skills `skill-creator` skill under the Apache-2.0 license: rewritten and shortened for
YAAP, with its eval scripts and viewer replaced by YAAP's eval cases, Compare and the trace, and with the
see-it-fail-first method of superpowers' `writing-skills` added.
