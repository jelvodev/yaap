---
sources: [superpowers/skills/test-driven-development, agency-agents/testing/testing-test-automation-engineer.md, own]
category: testing
summary: Write the test first and watch it fail, name it after the behaviour, and keep tests fast and deterministic.
rules:
- A bug fix starts with a test that fails because of the bug; it proves the cause and keeps the bug from returning.
- For new behaviour, write the test first and see it fail; a test you never saw fail may test nothing.
- Name each test after the behaviour it checks, so a failure says what broke.
- Wait for a condition, never a fixed sleep, and let each test make its own data; these cause most flaky tests.
- A flaky test is a bug; find its cause instead of retrying, skipping or deleting it.
---

# Testing

How tests are written and run. The rules above are always in the prompt; this text explains them.

## Test first

The order matters more than it seems. A test written after the code tends to check what the code does, not what it
should do, and it passes on the first run, so you never learn whether it can fail. Write the test, run it, and read
the failure: it should fail for the reason you expect (the missing behaviour), not because of a typo or an import
error. Then write the least code that makes it pass. The `test-driven-development` skill has the full cycle.

For a bug, the failing test is also the proof of the cause. If you cannot write a test that fails, you have not yet
found the bug.

## What a good test looks like

- **Named after behaviour:** `rejects_an_expired_token`, `totals_include_shipping`, not `test_token_2`.
- **One behaviour per test,** with the set-up, the action and the check easy to tell apart.
- **Checks results, not internals.** A test that asserts which private function was called breaks on every refactor
  and catches no bug.
- **Mocks only at the edge:** the network, the clock, a paid service. Mocking your own code tests the mock.
- **Table-driven** when the same logic runs on many inputs: one test, a list of cases, each case named.

## Deterministic and fast

A test that sometimes fails teaches everyone to ignore failures. The usual causes:

- a fixed sleep instead of waiting for the condition (the element visible, the response received, the file written);
- data shared between tests, or a test that relies on another having run first;
- the real clock, time zones or random values without a fixed seed;
- the order of a set or a dictionary taken as fixed.

When a test is flaky, treat it as a bug report: find which of these it is and fix it. A retry hides the cause and the
cause is often a real race in the product.

Keep most tests small and fast (no network, no browser), and a few end-to-end tests for the paths that matter most.
A slow suite stops being run.

## Running them

Run the tests that cover your change after every step, and the whole suite before you call the work done. Quote the
command and its result. For a web app, `start_process` runs it beside the chat so you can check the behaviour in the
preview too. Never weaken an assertion or skip a test to get a green run; say what fails instead.
