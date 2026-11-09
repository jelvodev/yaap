---
name: write-plan
description: Write an implementation plan before building a change that touches more than a few files, adds a
  dependency, changes a data model or follows an agreed design. The plan has small steps, each with its files and the
  command that checks it, and acceptance criteria in EARS form ("When <trigger>, the <system> shall <response>"). Use
  this whenever work is larger than a quick fix or will be handed to goal mode or a worker, because a wrong
  direction costs more than the plan.
license: MIT
metadata:
  sources: superpowers/skills/writing-plans, agency-agents/project-management/project-manager-senior.md, own
---

# Write a plan

A plan holds the decisions an implementer cannot make alone: which files, which names, which values, and how each
step is proven. Write it for someone who knows the language well but has never seen this project or this
conversation, such as a worker started with `delegate` or yourself after the context was compacted.

## 1. Gather what the plan argues from

- The design or request. If there is an agreed design (see the `brainstorm-design` skill), the plan follows it;
  where they disagree, the design wins or you ask.
- The code it touches: read the files, their tests and the project memory. Find the existing helpers to reuse.
- The project's standards (`load_standard`) and its test, lint and build commands.

If the request covers several independent parts, propose one plan per part. Each plan should leave working, tested
software behind on its own.

## 2. Write the acceptance criteria in EARS form

Acceptance criteria say what the finished change does, in sentences that can be tested one by one. EARS gives each
one a fixed shape, so a missing trigger or a vague response stands out:

| Kind | Shape |
|---|---|
| Event | When `<trigger>`, the `<system>` shall `<response>`. |
| State | While `<state>`, the `<system>` shall `<response>`. |
| Unwanted | If `<condition>`, then the `<system>` shall `<response>`. |
| Optional | Where `<feature is present>`, the `<system>` shall `<response>`. |
| Always | The `<system>` shall `<response>`. |

Examples:

- When the user submits the sign-up form with an empty email, the form shall show "Email is required" and keep the
  other fields filled in.
- While an export is running, the export button shall be disabled.
- If the payment provider does not answer within 10 seconds, then the checkout shall show a retry option and charge
  nothing.

One behaviour per criterion; "and" between two behaviours means two criteria. Use the exact values (limits, texts,
codes) from the design. Include the unwanted cases the design implies but does not spell out: empty input, a
duplicate, a timeout, no permission.

## 3. Map the files

Before the steps, list the files to create or change and what each one is responsible for. This is where structure
is decided: one responsibility per file, new code in its own module rather than added to a large one, and the
project's existing patterns over new ones.

## 4. Cut the work into steps

A step is the smallest piece that has its own test cycle and could be reviewed, accepted or rolled back on its own.
Each step names:

- **Files:** created or changed, with the function or section when it helps.
- **Change:** what to build, with the exact signatures and values where they matter. No code bodies the signature and
  the test already decide; a plan longer than the code it describes has written the code instead.
- **Test:** the test to write first, by name and what it asserts, and which criterion it proves.
- **Check:** the command to run and the result that means it passed.

Fold setup, configuration and documentation into the step that needs them. Order the steps so the software works
after each one.

## 5. Review the plan yourself

- **Coverage:** every criterion has a step whose test proves it.
- **No empty lines:** remove phrases that decide nothing ("handle edge cases", "add validation", "TBD", "write
  tests"). Replace each with the concrete case or drop it.
- **Consistent names:** a function called one thing in step 2 is not called something else in step 5.
- **Proportion:** if the plan is mostly code, replace bodies with signatures and test names.
- **Risk:** name the steps that are hard to undo (a migration, a public API, deleting data) and what protects them.

## 6. Hand it over

Save the plan where the project keeps plans, or `docs/plans/<topic>.md`, and open it beside the chat with
`open_document` so the user can read and edit it. Ask the user to approve it, and ask two things outright:

- **Commit per step?** YAAP's agent commits only when the user asks. If the user wants a commit after each green
  step, write "Commits: per step" in the plan; approving the plan then approves those commits, and nothing more
  (no push, no merge).
- **How should it run?**

  - **Here, step by step,** with the `execute-plan` skill.
  - **In goal mode:** `/goal Carry out docs/plans/<topic>.md`, which works through the steps without stopping
    between them.
  - **With workers:** steps delegated to the worker model and reviewed by a stronger one (the `execute-plan` skill
    describes how).

Build nothing until the plan is approved.

## Output template

```
# Plan: <topic>

Goal: <one sentence>
Design: <path to the design, or "the request in this conversation">
Approach: <two or three sentences>
Commits: per step | when the user asks

## Acceptance criteria
1. When <trigger>, the <system> shall <response>.
2. If <condition>, then the <system> shall <response>.

## Files
- `path/to/new_module.py` (new): <responsibility>
- `path/to/existing.py`: <what changes>

## Steps

### Step 1: <name>
- [ ] Files: `...`
- Change: <what, with signatures and values>
- Test: `test_<behaviour>` asserts <...> (criterion 1)
- Check: `<command>` passes; <expected output>

### Step 2: <name>
...

## Risks
- <step and what protects it, or "none">

## Not in this plan
- <what was left out on purpose>
```
