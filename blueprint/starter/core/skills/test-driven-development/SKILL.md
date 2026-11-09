---
name: test-driven-development
description: Build features and fix bugs test first, in short red, green, refactor cycles, and answer the usual
  reasons to skip the test. Use it whenever you are about to write or change production code, even for a small change,
  because a test you never saw fail proves nothing.
license: MIT
metadata:
  sources: superpowers/skills/test-driven-development, wshobson/plugins/tdd-workflows/commands/tdd-cycle.md
---

# Test-driven development

Write the test first, watch it fail, then write just enough code to make it pass. A test written after the code
passes at once, so it never showed it can catch the bug it is meant to catch.

## The cycle

### Red: one failing test

- Test one behaviour, named after that behaviour: `test_rejects_an_empty_email`, not `test_validate_2`. An "and" in
  the name means two tests.
- Use the real code. Mock only what you cannot run in a test (the network, the clock, a paid service), and only at
  the edge.
- Run it and read the output. It must **fail on its assertion**, for the reason you expect: the feature is missing.
  - An import or syntax error is not that failure yet. Add the empty function or class, run again, and see the
    assertion fail.
  - It passes at once? Then the behaviour already exists or the test checks the wrong thing. Fix the test before
    writing any code.

### Green: the simplest code that passes

- Write only what this test needs. No options, parameters or cases that no test asks for yet.
- Run the test, then the project's whole suite. "Green" means the suite, not just your file.
- The test fails? Change the code, not the test. Change the test only when the test itself was wrong, and say so.

### Refactor: clean up under green

- With all tests passing, remove duplication, improve names and extract a function where the code asks for it.
- Run the suite after each change. Add no behaviour in this phase; new behaviour starts with a new red test.
- Good triggers to refactor: the same three or more lines in several places, a function you cannot name in a few
  words, a function over about 60 lines, deep nesting or many branches.

Then the next behaviour, the next red test.

## A bug fix

Start with a test that reproduces the bug and fails. Fix the cause (the `systematic-debugging` skill finds it). The
test passes and stays, so the bug cannot come back unnoticed.

## Example

A slug function for article titles, in Python with pytest:

```python
# tests/test_slugs.py
from app.slugs import slugify


def test_slugify_lowercases_and_joins_words_with_hyphens():
    assert slugify("Hello World") == "hello-world"
```

`uv run pytest tests/test_slugs.py -q` stops at an import error, so add `def slugify(title: str) -> str: return ""`
in `app/slugs.py` and run again: it fails with `AssertionError`, as expected. Then the green step:

```python
def slugify(title: str) -> str:
    return "-".join(title.lower().split())
```

The test passes; run the whole suite. The next behaviour ("drops punctuation") gets its own red test first.

## Coverage

Use coverage of the lines you changed as a guide, not as a target to chase: look at what your change left unrun
and decide whether it needs a test. Every branch of the critical paths (money, permissions, deleting or losing data)
does. Keep to the project's own threshold when it has one. Coverage shows what was never run; it does not show that
the assertions are any good, so read the tests too.

## When test first does not fit

A throwaway spike to learn something, generated code, and pure configuration. Say so to the user when you skip the
test. A spike that turns out to be kept starts again, test first: keep what you learned, not the code.

## When skipping the test feels reasonable

The reasons to skip a test sound different each time, but most are one of these. Notice the thought, then write the
test anyway.

- **"The type checker covers it."** Types check shapes, not results: a total that is off by the shipping cost still
  has the right type.
- **"It is only a refactor."** Then the existing tests must stay green. If there are none, write tests that pin the
  current behaviour first (the `refactor-safely` skill).
- **"I will test it once the design settles."** Writing the test is how the design settles: it makes you use the
  interface from the caller's side before it exists.
- **"The user is waiting."** An untested fix tends to come back as a second request. The test is usually quicker
  than that second round.
- **"It needs a database, a browser or the network."** Split the logic from the outside world and test the logic
  alone; one integration test covers the seam.
- **"I watched it work in the preview."** One look is not a check that runs again after the next change. Turn the
  steps you clicked into a test.
- **"The test would only repeat the code."** Then it is aimed at the implementation. Test what a caller or a user
  gets back instead.

## In YAAP

- `/tdd <behaviour>` runs one cycle of this skill.
- Run tests with `run_command`; a test watcher runs as a background process with `start_process`, read with
  `read_process_output`.
- When `delegate` is available (in a project conversation), a batch of well-specified tests can go to the `tester`
  agent (`agent: tester`); check its diff and test run yourself afterwards. The `tester` itself writes them directly.

## Report

```
Behaviour: <one sentence>
Red: <test name>, <command> -> failed with <assertion>
Green: <command> -> <n> passed, suite <n> passed
Refactor: <what changed, or "nothing">
```
