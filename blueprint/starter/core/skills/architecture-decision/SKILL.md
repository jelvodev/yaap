---
name: architecture-decision
description: Write an architecture decision record (context, options, decision, consequences) where the project keeps
  them. Use it when a hard-to-undo choice is made (a framework, database, data model, API style, service split) or the
  user asks to record or justify a technical decision.
license: MIT
metadata:
  sources: wshobson/plugins/documentation-generation/skills/architecture-decision-records,
    agency-agents/engineering/engineering-software-architect.md
---

# Architecture decision

Code shows what was built, not why, and not what was rejected. An ADR keeps the reason, so that a later change can
tell whether the reason still holds instead of guessing or repeating an old debate.

## When an ADR is worth it

Write one when a decision is costly to reverse, affects more than one part of the system, or picks between options
that reasonable people would weigh differently. Skip it for bug fixes, routine upgrades and choices that one small
commit can undo; a line in the commit message is enough there.

## 1. Find where ADRs live

Look for an existing folder: `docs/adr/`, `docs/decisions/`, `adr/`, `architecture/decisions/`. Follow what is there:
its numbering, file names, template and index. When there is none, use `docs/adr/NNNN-short-title.md` with four-digit
numbers starting at `0001`, and a `docs/adr/README.md` that lists them. Ask the user before starting a new folder in
a project that has its own documentation layout.

Read the ADRs that touch the same area. A new decision may supersede one of them, and must say so.

## 2. Gather the context

State the problem without the solution in it. Collect the forces: requirements, constraints (team, budget, time,
existing systems), quality attributes that matter here (latency, cost, safety, simplicity) and what happens if
nothing is decided. Read the code involved; a decision about code you have not read is a guess.

## 3. Weigh real options

List two to four options that could actually be chosen, including "keep what we have" when it is viable. For each,
give the honest pros and cons, and what it costs to reverse later. An option list with one serious candidate and two
straw men tells the reader nothing. Name what is given up, not only what is gained.

When the choice is unclear, say so and ask the user; the ADR records a decision, it does not make it on its own.

## 4. Write the decision

One or two sentences, in active voice: "We store sessions in the database, not in signed cookies." Then the
consequences: what becomes easier, what becomes harder, what must now be done (migrations, follow-up work), and which
risks remain. Negative consequences belong here too; leaving them out is how the same debate comes back.

Keep it to a page or two. Link, do not copy, long designs or benchmarks.

## 5. Status and changes

Statuses: *Proposed* (under discussion), *Accepted*, *Rejected*, *Deprecated*, *Superseded by NNNN*. Once accepted,
an ADR is not rewritten: a new decision gets a new ADR that supersedes it, and the old one only gets its status
changed and a link. That keeps the history of why things changed.

## In YAAP

- Add one line to the project memory with `update_memory` under *Decisions*, pointing to the ADR, so every later
  conversation sees the decision without reading the folder: "Sessions live in the database (docs/adr/0007)."
- For a decision that needs a design first, use the `brainstorm-design` skill, then record the outcome here.
- When the user wants to edit the draft together, open it in the document panel with `open_document` (in a project
  conversation); elsewhere, show the draft in the reply.

## Template

```markdown
# NNNN. <Decision as a short phrase>

- Status: Proposed | Accepted | Rejected | Deprecated | Superseded by [NNNN](NNNN-title.md)
- Date: YYYY-MM-DD
- Deciders: <names or roles>

## Context

<The problem and the forces at play, without the solution. What happens if nothing is decided.>

## Options

### <Option A>
- Good: <...>
- Bad: <...>
- Cost to reverse: <low / medium / high, and why>

### <Option B>
...

## Decision

<We will ... , because ...>

## Consequences

- Easier: <...>
- Harder: <...>
- To do: <follow-up work, migrations>
- Risks: <what could prove this wrong, and how we would notice>

## Links

- Supersedes / related to: <ADR links>
- <issues, designs, benchmarks>
```
