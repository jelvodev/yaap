---
name: frontend-design
description: Give a new page, screen or component a deliberate visual identity instead of the look every generated
  interface shares. Use this whenever you build new UI, restyle an existing one, or are asked to make something look
  better, more distinctive or less generic, also for a small landing page or a single component, because the default
  choices are made in the first minutes and are hard to undo later.
license: Apache-2.0
metadata:
  sources: anthropic-skills/skills/frontend-design
---

# Frontend design

A generated interface tends to look like every other one: the same card grid, the same accent, the same labels above
every heading. Users read that sameness as "nobody thought about this". Make each visual choice on purpose, for this
subject, and check it before building.

When the project already has a design system or a brand, follow it. This skill is for the choices it leaves open.

## 1. Know the subject

Before any colour or font, write three lines: what this is about, who uses it, and the one job the page must do. If
the request does not say, propose them and ask (the `brainstorm-design` skill helps). Read the project memory for
earlier design decisions.

Distinctive choices come from the subject's own world: its materials, its vocabulary, how its people already work. A
seed catalogue and a trading dashboard should not end up with the same layout. Use real content from the start;
placeholder text hides whether the design works.

## 2. Write a short design plan

Keep it to a dozen lines:

```
Subject: <what, for whom, the page's one job>
Palette: <4 to 6 named colours with their hex values and roles>
Type: <one or two families and what each is for; the type scale>
Layout: <one sentence, plus a small ASCII sketch; alignment>
The one bold thing: <the element people will remember>
Principles: <two or three rules that make this page itself>
```

## 3. Check the plan against the defaults

Read the plan as a critic. For each line ask: would I write the same line for any similar request? If yes, it is a
default, not a choice; change it and say why. Common defaults that mark a page as generated:

- a warm off-white background with a big serif headline and one earthy accent, or a black page with one neon accent;
- the same rounded card, shadow and radius on everything, whatever its importance;
- a small uppercase label above every heading, numbered markers on content that is not a sequence;
- one word of a headline set in another colour or style;
- every section fading and sliding in as it scrolls into view;
- an arrow appended to every link and button.

Each of these can be right for a specific brief. When the request asks for one, do it. When the request leaves the
choice open, do not spend it on a default.

## 4. Build with care

- **Typography carries the voice.** Pick the faces for this subject, not the usual ones. One family is often enough;
  a second must differ clearly. Set a type scale with a few steps and deliberate weights. Keep body lines under about
  75 characters, and give a serif body a little more line height.
- **Structure means something.** Borders, dividers, numbers and labels tell the reader how content relates. Use them
  only where that relation is real.
- **Spend boldness once.** One memorable element; everything around it stays calm. Remove decoration that serves
  nothing.
- **Motion with a reason.** Motion that answers an action (opening, confirming, moving an item) helps. Motion nobody
  asked for is used once, for the one moment that matters, and never without `prefers-reduced-motion`.
- **Mind the CSS.** Keep selector specificity flat (one class per rule where possible) so spacing rules do not cancel
  each other between sections. Put the plan's colours and sizes in custom properties or the project's tokens.

## 5. The quality floor

Never ship below it, and do not make a feature of it: responsive down to a phone, visible keyboard focus, contrast
that meets WCAG AA, reduced motion respected, no layout shift as fonts and images load. The `web` standard and the
`accessibility-audit` skill have the details.

## 6. Words are design

Copy can look generated as easily as a layout can.

- Name things by what users know, not by how the system is built: "Notifications", not "Webhook settings".
- A button says what happens: "Save changes", not "Submit". Keep that word through the flow: a "Publish" button
  leads to a "Published" message.
- An error says what went wrong and how to fix it, plainly and without apology. An empty state says what to do first.
- Sentence case, plain verbs, no filler. Each piece of text does one job.

## 7. Look, then remove one thing

Run the app with `start_process` and its port so it shows in the preview, or use `open_browser`, and take screenshots
at desktop width and at about 390 pixels. Compare them with the plan. Then remove one thing: the decoration, the
extra colour or the effect that adds least. Check the states too: empty, loading, error, very long text.

Record the palette, type and the one bold thing with `update_memory`, so the next screen in this project builds on
them instead of starting over.

## Report

```
Subject: <one line>
Plan: <palette, type, layout, the bold thing, in four short lines>
Defaults avoided: <which ones, and what you chose instead>
Checked: <screenshots taken, widths, states>
Not checked: <what is left, or "nothing">
```

Adapted from the `frontend-design` skill of anthropics/skills under Apache-2.0: rewritten and shortened, the
brand-specific calibration removed, and YAAP's preview, `open_browser` and project memory added.
