---
name: ui-finish-gate
description: Decide whether a screen or UI change is ready to ship by walking its main task in the running app, at
  desktop and phone width and in every state, and naming what must change first. Use it before a UI change is merged
  or released, when asked if a screen is ready, or when it feels generic.
license: MIT
metadata:
  sources: agency-agents/design/design-ui-finish-gate-reviewer.md
---

# UI finish gate

A screen is ready when its users can do what they came for, in every state it can be in, at every width they use.
Check that in the running app, since the code and a mockup both hide what a user meets, and end with a verdict:
ready, or not ready with a short list of what must change.

## 1. Name the job

Write two or three sentences: who opens this screen, the task they come to complete, and what they need to see
before they act. Take it from the request, the design notes and the project memory, and mark what you had to guess.
Note the fixed constraints too (the design system, the brand, the components the project already has): a change
that breaks them is not a fix.

## 2. Walk the task

Start the app with `start_process` and its port, so it shows in the preview beside the chat, or use `open_browser`
when a server already runs. Do the main task the way a user would, from arriving on the screen to the result, and
note every moment you hesitate: where to look, what to press, whether it worked. Then do it again with the keyboard
only. Each hesitation is a candidate finding.

## 3. Fill the grid

Check every state at two widths, about 1440 and about 390 pixels, and keep a screenshot of each (`open_browser`):

| State | Desktop | Phone |
|---|---|---|
| Typical data | | |
| Loading | | |
| Empty, as on first use | | |
| Error | | |
| Long text, missing values, many rows | | |
| Focus, selected and disabled controls | | |

Write "ok" or the problem in each cell. An empty state tells the user how to begin; an error tells them how to
recover; the phone layout keeps what users scan for instead of piling the desktop blocks on top of each other.

## 4. Look for sameness

A screen that could belong to any product usually hides the user's work behind stock parts. Signs:

- everything has the same weight, so nothing comes first;
- a layout pattern picked by habit (tiles, a banner, a gallery) where the task needs a table, a form or a list;
- decoration that carries no information;
- components and words that differ from the rest of the product.

A sign is a finding only when you can say what the user loses and what would fit this task better. Plain is fine;
interchangeable is the problem.

Accessibility is part of ready: a keyboard trap, a control without a name or text below contrast keeps the screen
from shipping. For a full audit, load the `accessibility-audit` skill.

## 5. Write findings that can be checked

Each finding gives the place (state, width, screenshot), what the user runs into, and the change, with how to confirm
it. Taste stays out: "feels busy" is not a finding; "at 390 pixels Save and Delete sit side by side in the same
style" is. When the constraints explain a choice, leave it alone.

## 6. Give the verdict

- **Ready:** the walk had no blocking hesitation, no cell in the grid blocks, and every stock part has a reason.
- **Not ready:** at least one must-change remains. List each one as a requirement, not as a suggestion.

Name what works well too, so nobody removes it by accident.

When you have `delegate` (in a project conversation), a larger review can go to the `frontend-reviewer` agent
(`delegate` with `agent: frontend-reviewer`), which reads with a stronger model. That agent runs this gate itself.

## Report

```
Verdict: ready | not ready - <screen or change>
Job: <who, the task, what they need to see first>

Walk: <the task walked>; hesitations: <n>
Grid: <states and widths checked>; open cells: <list, or "none">

Must change
- [<state, width>] <what the user runs into> -> <change>; confirm by <check>

Could change
- <...>

Works
- <...>

Not checked: <...>
```
