---
name: brainstorm-design
description: Agree on a short design with the user before building a feature, a new component, a change in behaviour
  or anything with more than one sensible approach. Asks one question at a time, proposes approaches with a pick,
  shows UI ideas in the preview beside the chat, and writes the agreed design down. Use this whenever the request
  leaves open what to build or how, even when it sounds simple, because building the wrong thing well is the most
  expensive mistake there is.
license: MIT
metadata:
  sources: superpowers/skills/brainstorming, own
---

# Brainstorm a design

Find out what the user actually wants, and why, before anything gets built. When you know the why, the hundred small
decisions nobody mentions come out the way the user would have made them.

## 0. Is a design needed?

- **Everything that matters is in the request** ("rename the button to Save"): do it and say what you did. No
  design.
- **Something that changes what gets built is open**: start here.
- **The user says "just build it"**: state the one or two main choices you are making (language, library, where it
  lives) in a line each, then go.

## 1. Look before you ask

Read what the request touches: the files, their tests, the project memory, similar features. Keep reading until you
can say how the relevant part works today and what the change would touch. Never ask what the code or the
conversation already answers, and never reopen what the user already decided.

For a wide search, send an exploring worker with `delegate` (mode `explore`), so the conversation stays small.

## 2. One question at a time

Ask only what changes what gets built and what neither the request nor the project answers. Everything else is your
call: make it, say it as a proposal with a reason, and move on.

- **Start with the why.** Until you know what the user wants to achieve, ask open questions without options to pick
  from; options steer, and their own words carry intent.
- **Then the choices.** Once the why is clear, a choice with named options gets each option in a line, with what it
  costs, and which one you would pick.
- **One question per message**, specific to this project, built on the last answer or on something you found. Say
  what the answer would change.
- **Fill gaps out loud.** A default the user never heard is one they could not reject.

Cover, as territory and not as a script: the goal and what success looks like, what is out of scope, what would make
it a failure even if it works, what exists today, and the details the user cares about.

## 3. Lay out the approaches

Before writing anything up, give the ways to build it a message of their own: two or three real approaches, grounded
in how the project already works, each with its trade-off. Drop the ones that fight the goal or the existing code.
Recommend one and say why. When there is truly only one sensible way, say so and why the others do not fit.

## 4. Show, do not describe

When seeing beats reading (a screen, a layout, a flow, options that look different), show it:

- **A quick sketch:** a few lines of text, or a complete `html` page in a code block, which the chat shows as a live
  preview.
- **In the real app:** write a throwaway mockup page or route, start the dev server with `start_process` and its
  port, and point the user to the preview beside the chat. Put two options side by side on one page when the choice
  is between them. Check what the user will see yourself with the preview's screenshot or `open_browser`.
- Keep mockups out of the real code: a scratch folder or a route you remove afterwards, never committed. They answer
  a question; they are not the first version.

Requirements, scope and trade-offs stay in text; a question about a UI is not always a visual question.

## 5. Write the short design

Play back what you understood in a few short parts as you go, and end each with "what is wrong or missing?". Mark
what the user said as theirs and what you filled in as your proposal.

When the approach is chosen, write the design with the template below. A small change needs a few lines per heading;
a larger one more, but still short enough to read in two minutes. Save it where the project keeps such documents, or
`docs/designs/<topic>.md`, and open it beside the chat with `open_document` so the user can edit it directly. A
decision that holds beyond this change (a convention, a library choice) goes into the project memory with
`update_memory`.

If the work turns out to be several independent pieces, say so, agree on an order and design them one at a time.

## The gate

Build nothing until the user approves the design: no product code, no scaffolding, no new dependencies. Reading,
exploring and throwaway mockups are fine. "Sounds good" to an idea is not approval of a document you have not
written yet.

After approval, the next step is a plan: load the `write-plan` skill with `load_skill`. A design small enough to
need no plan goes straight to building, with the design's checks as the definition of done.

## Output template

```
# Design: <topic>

Goal: <what it does for whom, and why now>
Success: <how we will know it works>
Out of scope: <what this change will not do>

## Approaches
- <A, chosen>: <how, trade-off>
- <B>: <how, why not>

## Design
- <the decisions, one line each; mark your own calls as "proposed">

## Open
- <what is left to the builder, or "nothing">
```
