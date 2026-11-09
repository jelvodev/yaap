---
name: e2e-testing
description: Write and fix Playwright end-to-end tests that find elements by role and label, wait on what the user
  sees instead of sleeping, and run against the app in the preview. Use it whenever you add or change a user journey,
  write a browser test, chase a flaky one, or must prove a UI change in a browser.
license: Apache-2.0
metadata:
  sources: wshobson/plugins/developer-essentials/skills/e2e-testing-patterns, agency-agents/testing/testing-test-automation-engineer.md, anthropic-skills/skills/webapp-testing, own
---

# End-to-end testing

An end-to-end test drives the real app in a real browser the way a user does. It is slow and costly to keep, so use it
for the journeys that must never break and leave the details to unit and component tests.

## 1. Choose the journeys

List the few flows that would hurt most if they broke: signing in, the main task of the app, paying, saving work. One
test per journey, plus one per important failure (a wrong password, a server error). Edge cases of a single function
belong in its unit tests.

## 2. Run the app first

Start the app with `start_process` and its port, so it shows in the preview beside the chat and the tests have a
server to talk to. Point Playwright at it with `baseURL`, and let the config start it when it is not running:

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  use: { baseURL: "http://localhost:5173", trace: "on-first-retry" },
  retries: process.env.CI ? 2 : 0,
  webServer: { command: "pnpm dev", url: "http://localhost:5173", reuseExistingServer: true },
});
```

Use the project's own dev command and port.

## 3. Look before you write

Do not guess selectors from the source. Open the running page with `open_browser`, go through the journey once by
hand, and read what the page offers: the roles and accessible names of buttons, fields and headings, and what appears
after each step. Write the test from what you saw. When a control has no usable name, that is an accessibility bug;
fix the markup rather than reaching for a CSS selector.

## 4. Write the test

```ts
import { expect, test } from "@playwright/test";

test("a user can rename a project", async ({ page }) => {
  await page.goto("/projects/demo");

  await page.getByRole("button", { name: "Rename" }).click();
  await page.getByRole("textbox", { name: "Project name" }).fill("Demo two");
  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.getByRole("heading", { name: "Demo two" })).toBeVisible();
});
```

- **Find by role, label or text,** in that order (`getByRole`, `getByLabel`, `getByText`). They follow what the user
  sees and survive restyling. A `data-testid` is the fallback for elements with no role or text; CSS classes and
  `nth-child` break at the next refactor.
- **Assert what the user sees.** `expect(locator).toBeVisible()`, `toHaveText()`, `toHaveURL()`: these retry until
  they pass or time out, so they wait exactly as long as needed.
- **No fixed sleeps.** `waitForTimeout` is either too short (flaky) or too long (slow). Wait on a visible result or a
  network response (`page.waitForResponse`) instead.
- **Name the test after the behaviour,** as a sentence a user would recognise.

## 5. Keep tests independent

Each test makes its own data (through the API or a fixture) and does not depend on another test having run. Sign in
once in a setup project and reuse the saved storage state, rather than going through the login form in every test.
Tests that share data cannot run in parallel and fail in confusing ways.

Group repeated steps in a small helper or page object only when three tests repeat them; a page object for every page
is more code to keep than it saves.

## 6. Watch the console

A page can look right while throwing errors. An automatic fixture collects them and fails the test when there are
any:

```ts
import { expect, test as base } from "@playwright/test";

export const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
      });
      page.on("pageerror", (error) => errors.push(error.message));
      await use(errors);
      expect(errors).toEqual([]);
    },
    { auto: true },
  ],
});
```

The tests then import `test` from this file instead of from `@playwright/test`. When you check a page by hand with
`open_browser`, read its console messages there too.

## 7. When a test fails or flakes

A flaky test is a bug in the test or the app, never something to retry until green. Run it alone and repeated
(`--repeat-each=10`) to see it. Open the trace (`trace: "on-first-retry"` records one) to see each step, the DOM and
the network at that moment. Typical causes: a fixed sleep, a selector that matches two elements, data left by another
test, an animation still running. The `systematic-debugging` skill has the method.

## 8. Run and report

Run the suite with the project's script (for example `pnpm exec playwright test`) and read the output. Report:

```
Journeys covered: <list>
Command: <what you ran>
Result: <passed, failed, flaky; with the failing test and its error>
Not covered: <journeys or browsers left out>
```

Partly adapted from the `webapp-testing` skill of anthropics/skills under Apache-2.0: its approach of looking at the
running page before writing a test and collecting console errors, rewritten as guidance without its scripts.
