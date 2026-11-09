---
name: ui-finish-gate
description: Decide PASS or HOLD before a screen or UI change ships, by checking the running interface against the
  product's user and main task, its states and its narrow-screen layout, with evidence for every finding. Use this
  when a UI change is about to be merged or released, when asked to review a screen or say whether it is ready, or
  when an interface feels generic or interchangeable but nobody can say why.
license: MIT
metadata:
  sources: agency-agents/design/design-ui-finish-gate-reviewer.md
---

# UI finish gate

The last check before a UI ships. It is not a taste review: it asks whether this screen does its job for its users,
in every state, at every width, and it ends with a decision the team can act on. Review the running app, not the
code alone and not a mockup.

## 1. Write the product lens

Before judging anything, write one short paragraph:

- who uses this screen and what they come to finish;
- the object or decision they must see first (the first read);
- the one primary action;
- what they do daily, and what is rare but risky;
- the constraints that stay: the design system, the brand, the framework.

If you have to guess part of it, mark the guess as an assumption. Read the project memory for earlier decisions.

## 2. Open it

Run the app with `start_process` and its port so it shows in the preview beside the chat, or use `open_browser`
when a server already runs. Take screenshots at desktop width (about 1440 pixels) and phone width (about 390). Bring
up each state: loading, empty, an error, a long or missing value, a selected row, a disabled control, keyboard focus.

## 3. Review in this order

1. **The first read.** Can a new user tell, in the first screen without scrolling, what this is and what to do?
2. **Hierarchy.** Does visual weight follow the user's decisions, or the component library's defaults? Four equal
   cards for unequal numbers is a finding.
3. **Fit.** Does each layout choice earn its place for this task? A card grid where a table is needed, a hero where
   people come to work, decoration (gradients, glass, large radii, animation) that serves nothing.
4. **States.** Are loading, empty, error, focus and disabled states designed and useful? An empty state says what to
   do first; an error says how to fix it.
5. **Narrow screens.** Does the phone layout keep what users scan for, or does it only stack the desktop pieces?
6. **Consistency.** Are the project's tokens, components and words used as elsewhere in the product?

Accessibility is part of finished, not a later clean-up: a keyboard trap or missing labels are findings here. For a
full check, load the `accessibility-audit` skill.

## 4. Evidence, not adjectives

Every finding names what you saw and where (a screenshot, a state, a width) and why it breaks the lens. "Looks
cluttered" is not a finding; "the cancel action and the delete action look the same at 390 pixels" is. A simple
screen is not a problem; an interchangeable one is.

Separate a real problem from your preference. If the product's constraints explain a choice, keep it.

## 5. Decide

- **PASS** when the first read and the primary action are clear, the named states and both widths have been checked,
  and no generic default remains without a reason.
- **HOLD** when a required change remains. Do not soften a hold into a list of suggestions; say what must change and
  how it will be checked.

Name what already works, so nobody rewrites it by accident.

## Report

```
UI finish gate: <screen or change>
Decision: PASS | HOLD

Lens: <user, task, first read, primary action>

Evidence
- <what you saw, where> -> <why it matters for this user>

Required before PASS
1. <concrete change> - check with <state and width>

Optional
- <refinements that do not hold the release>

Keep
- <decisions that already serve the product>

Checked: <widths, states, screenshots>
Not checked: <what is left>
```
