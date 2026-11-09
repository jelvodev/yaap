---
name: product-requirements
description: Write a product requirements document (PRD) that starts from the problem and its evidence, with
  measurable goals, non-goals, user stories with acceptance criteria, risks and how success is measured. Use it when
  the user wants to specify a feature, turn an idea into requirements or scope a project.
license: MIT
metadata:
  sources: agency-agents/product/product-manager.md
---

# Product requirements

A requirements document exists so that everyone builds the same thing for the same reason, and can tell afterwards
whether it worked. Most bad PRDs describe a solution and skip the problem; start from the problem.

## 1. The problem before the solution

A request usually arrives as a solution ("add an export button"). Ask why until you reach the user's need or the
business goal behind it ("finance re-types the monthly totals into a spreadsheet, about two hours a month per
customer"). Then check other ways to meet that need before settling on the requested one.

Collect evidence for the problem and write down where each piece came from: user interviews, support tickets,
usage data, sales feedback, what competitors offer. When there is none, say so: the PRD then describes a bet, and the
first step may be a cheap test of it.

If you cannot state in one short paragraph why the reader's users will care, stop and work that out with the user
first.

## 2. Write the document

Use this shape. Leave a section out only when it truly does not apply, and keep the whole under about two pages for
a feature; link to detail.

```markdown
# <Feature or initiative>
Status: draft | in review | approved | building | shipped
Owner: <name>  Updated: <date>

## Problem
<Who has the problem, how often, what it costs them now. One paragraph.>
Evidence: <each item with its source>

## Goals and measures
| Goal | Measure | Now | Target | When measured |
|---|---|---|---|---|

## Non-goals
- <what this deliberately does not do, and why>

## Users and stories
As a <user>, I want <action> so that <outcome>.
- When <situation>, the system shall <response>.
- If <unwanted case>, then the system shall <response>.

## Solution
<The approach in a few paragraphs. Key decisions: chose A over B because ..., giving up ...>

## Risks and dependencies
| Risk | Likelihood | Impact | What we do about it |
|---|---|---|---|

## Open questions
- <question>, owner, needed by <date>

## Launch and measurement
<Who gets it first, how it widens, what result stops or reverses the rollout, when the measures are read.>
```

## 3. Make the goals measurable

- Each goal has a measure, the value today, a target and a moment to read it. "Improve onboarding" is not a goal;
  "share of new accounts that finish setup in the first week, from 40% to 60%, read 60 days after launch" is.
- Measure outcomes for users or the business, not output ("shipped five screens").
- Add one guard measure that must not get worse (support tickets, page load time, error rate), so a gain is not
  bought with a loss elsewhere.
- When there is no current value, the first task is to start measuring it.

## 4. Acceptance criteria that can be tested

Write each criterion so a tester can say pass or fail: a situation and the response, in the forms above. Cover the
normal case, the edge cases (empty, too large, no permission) and what happens on failure. A criterion that needs a
judgment call ("fast", "intuitive") gets a number or an example.

## 5. Non-goals and scope

Non-goals prevent the scope from growing quietly. List what people will likely expect but this version does not do.
When a new request arrives during the work, compare it with the goals: accept it with what it pushes out, defer it,
or decline it, and record the choice in the document.

## 6. Review and after launch

- Ask the user who must agree (engineering, design, support, legal) and what each needs to see.
- When `delegate` is available (in a project conversation), use an exploring worker as a fresh reader: give it the
  file and ask what it would build from it and what is missing; its gaps are the document's gaps. Otherwise ask the
  user to have someone from engineering read it with the same two questions.
- After launch, read the measures at the agreed moment and add the result to the document: met, missed, and what
  was learned. A PRD without that last step cannot tell anyone whether the work paid off.

For ranking several candidate features against each other, use the `prioritise` skill.

## Report

End with the path of the document, the open questions with their owners, and which goals still lack a current value.
