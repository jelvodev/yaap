---
name: accessibility-audit
description: Audit a page, component or flow against WCAG 2.2 level AA with an automated axe scan and a manual check
  by keyboard, zoom and screen-reader semantics, and report each issue with its success criterion, severity and fix.
  Use this before a UI ships, after building a new component (dialog, menu, tabs, form, table), when asked whether
  something is accessible, or when a Lighthouse or axe score looks fine, because automated tools find only a part of
  the real problems.
license: MIT
metadata:
  sources: agency-agents/testing/testing-accessibility-auditor.md
---

# Accessibility audit

An audit answers one question: can people who use a keyboard, a screen reader, zoom or voice control do what this
screen is for? A clean automated scan does not answer it; scanners catch roughly a third of the issues. Do both
parts below, and report only what you checked.

## 1. Scope

Name the pages or components and the task each must support ("find an order and cancel it"). Start the app with
`start_process` and its port so it runs in the preview, and open it with `open_browser`.

## 2. Automated scan

Run axe on every page and state in scope. In a Playwright test it is a few lines and stays as a regression check:

```ts
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("the orders page has no detectable WCAG A or AA violations", async ({ page }) => {
  await page.goto("/orders");
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(results.violations).toEqual([]);
});
```

Scan the states too: an open dialog, a form with errors, an empty list. A violation in a state nobody scanned still
ships.

## 3. Keyboard

Go through the task with the keyboard only, using the browser's key presses and a screenshot after each step.

- Every interactive element is reachable with Tab, in an order that follows the layout.
- The focus is always visible and never hidden behind a sticky header or a banner (2.4.7, 2.4.11).
- Enter and Space activate buttons; arrow keys move within tabs, menus, radio groups and grids.
- Escape closes a dialog or menu, and focus returns to the control that opened it.
- There is no trap: you can always Tab away.
- A skip link or landmarks let you pass the navigation.

## 4. Semantics

Read the accessibility tree or the markup for what a screen reader will announce.

- Every control has an accessible name that matches its visible label (4.1.2, 2.5.3).
- Headings form an outline in order; landmarks (`header`, `nav`, `main`, `footer`) are present once each where they
  apply.
- Images that inform have alt text; decorative ones have `alt=""`.
- Form fields have labels; errors are tied to their field (`aria-describedby`) and announced.
- Status messages ("Saved", "3 results") are in a live region, so they are announced without moving focus (4.1.3).
- Custom widgets follow the matching ARIA pattern exactly; a native element is better wherever one exists.

## 5. Visual and motion

- Text contrast is at least 4.5:1, large text and interface parts 3:1 (1.4.3, 1.4.11).
- At 200% zoom nothing is cut off, and at 320 pixels wide the content reflows without horizontal scrolling (1.4.4,
  1.4.10).
- Colour is never the only signal; an error also has text or an icon (1.4.1).
- Targets are at least 24 by 24 pixels or well spaced (2.5.8).
- Animation stops or reduces under `prefers-reduced-motion`; nothing flashes more than three times a second.

## 6. The newer WCAG 2.2 criteria

Check these explicitly, scanners barely cover them: focus not obscured (2.4.11), an alternative to dragging (2.5.7),
target size (2.5.8), help in the same place on every page (3.2.6), no asking again for what the user already entered
(3.3.7), and sign-in without a memory or puzzle test, so pasting a password works (3.3.8).

## 7. Severity

| Severity | Meaning |
|---|---|
| Critical | Blocks the task for some users (a keyboard trap, an unlabelled submit button) |
| Serious | The task is possible only with a workaround or great effort |
| Moderate | Causes difficulty, but the task can be done |
| Minor | An annoyance that does not stop anyone |

Critical and serious issues hold a release.

## Report

```
Scope: <pages, components, states; the task checked>
Method: <axe scan (which states), keyboard pass, semantics, zoom and contrast>

Issues
1. <title> | <WCAG criterion number and name> | <severity>
   Where: <page, component, file and line if known>
   Found: <what happens, and how you saw it>
   Fix: <the concrete change>

Works well: <patterns worth keeping>
Not checked: <for example a real screen reader, voice control, other browsers>
```

Say plainly that a check by a real screen-reader user is still worth doing when the stakes are high; this audit
approximates it.
