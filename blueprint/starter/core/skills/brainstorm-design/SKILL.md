---
name: brainstorm-design
description: Settle with the user what to build and how before building a feature, a component or a change in
  behaviour that has more than one sensible approach. Asks short questions, compares routes, sketches screens in the
  preview and writes a short design. Use it whenever the request leaves the what or the how open.
license: MIT
metadata:
  sources: superpowers/skills/brainstorming, own
---

# Brainstorm a design

A request names a result. A design says which result, for whom, and by which route. Settle those with the user while
changing them costs nothing, and write them down, so the plan and the build follow what the user meant and not what
you assumed.

## When to skip it

- Nothing in the request is open that would change the result ("rename the button to Save"): build it and say what
  you did.
- The user wants no discussion: name the main choices you will make (language, library, where the code goes), one
  line each, and start.
- Anything else: go through the steps below. A small change gets a short pass, not a long one.

## Step 1: Do your homework

Before your first question, learn how things work today: the files the request touches and their tests, the project
memory, a similar feature elsewhere in the project. A question the project can answer is yours to answer, and a
point the user already settled stays settled. In a project conversation, send a wide search to an exploring worker
(`delegate`, mode `explore`) so the conversation stays small.

## Step 2: Settle the purpose

The part users most often leave out is the reason. Ask for it plainly, and let the user answer in their own words
before you offer any choices; a list of options puts words in their mouth. Worth learning:

- the problem it solves, and for whom;
- how the user will tell that it works;
- what must stay as it is;
- what it must not turn into.

Ask about one thing per message, tie the question to something concrete you found, and say what depends on the
answer. Whatever you can decide yourself, decide, and say it out loud as a proposal with its reason, so the user can
overrule it.

## Step 3: Compare routes

When the purpose is clear, send a message that does nothing but compare ways to build it. Two or three routes, each
grounded in how this project works, in a small table: how it works, what it costs, what it risks. Leave out routes
that conflict with the purpose or the existing code. End with the route you recommend and why. If only one route makes
sense, say so and say what rules out the others.

## Step 4: Let the user see it

When the choice is about something visible (a screen, a layout, a flow, variants that look different):

- **Small:** a text sketch, or a self-contained `html` page in a code block, which the chat renders as a live
  preview.
- **In the real app:** a throwaway page or route, the dev server started with `start_process` and its port, the
  result in the preview beside the chat. Two variants on one page make the choice quick. Look at it yourself with
  `open_browser`; the preview's *Screenshot* button is for the user.
- Throwaway work lives in a scratch folder or a temporary route, is never committed, and goes once the question is
  answered. It is not the first version of the feature.

Questions about scope, rules or trade-offs stay in text, even when the feature has a screen.

## Step 5: Write the design

Summarise as you go: each time a part is clear, describe it back in a short paragraph and ask the user to correct
it. Keep the user's words and your proposals apart ("you said", "I propose").

When the route is chosen, fill in the template below; it should take two minutes to read. Save it where the project
keeps design notes, or in `docs/designs/<topic>.md`. In a project conversation, open it beside the chat with
`open_document` so the user can edit it; elsewhere, give the design in the reply. A decision that outlasts this
change (a convention, a library) goes into the project memory with `update_memory`.

If the request turns out to be several separate pieces, agree on an order and design them one at a time.

## Hold the build

Until the user approves the written design, write no product code, add no dependencies and create no scaffolding.
Reading, exploring and throwaway sketches are fine. Only the written design can be approved; agreeing with an idea
earlier in the conversation does not count.

Next comes the plan: load the `write-plan` skill with `load_skill`. A change too small to need a plan is built
straight away, with the design's "Done when" as its check.

## Template

```
# Design: <topic>

Purpose: <the problem, for whom, and why now>
Done when: <how the user will tell it works>
Keeps: <what must not change>
Not included: <what is left out on purpose>

## Routes
| Route | Cost | Risk | |
|---|---|---|---|
| <A> | <...> | <...> | chosen |
| <B> | <...> | <...> | not chosen: <why> |

## Decisions
- <decision> (user | proposed)

## Left to the builder
- <..., or "nothing">
```
