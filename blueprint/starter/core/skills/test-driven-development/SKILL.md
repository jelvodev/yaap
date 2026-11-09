---
name: test-driven-development
description: Build features and fix bugs test first, in short red, green, refactor cycles, and answer the usual
  excuses for skipping the test. Use this whenever you are about to write or change production code, for a new
  feature, a bug fix, a refactor or a change in behaviour, even a small one, because a test you never saw fail
  proves nothing.
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

Follow the project's coverage target. Without one: about 80% of the lines you changed, and every branch of the
critical paths (money, permissions, deleting or losing data). Coverage shows what was never run; it does not show
that the assertions are any good, so read the tests too.

## When test first does not fit

A throwaway spike to learn something, generated code, and pure configuration. Say so to the user when you skip the
test. A spike that turns out to be kept starts again, test first: keep what you learned, not the code.

## Answers to the usual excuses

| Excuse | Answer |
|---|---|
| "It is too simple to test." | Simple code breaks too, and its test takes a minute to write. |
| "I will add the tests later." | Tests written later pass at once and check what the code does, not what it should. |
| "I already tried it by hand." | A manual try leaves no record and is not repeated after the next change. |
| "This code has no tests yet." | Then the first test you add is the most valuable one. Start where you change it. |
| "Writing the test is hard." | Code that is hard to test is hard to use. Simplify the interface first. |
| "I need to explore first." | Fine: explore, throw the experiment away, then build it test first. |
| "Deleting my code is a waste." | The time is spent either way. Code without a test you saw fail is the real cost. |
| "It slows me down." | It is slower than skipping the test today and faster than debugging it next month. |

## In YAAP

- `/tdd <behaviour>` runs one cycle of this skill.
- Run tests with `run_command`; a test watcher runs as a background process with `start_process`, read with
  `read_process_output`.
- Hand a batch of well-specified tests to the `tester` agent with `delegate`, and check its diff and test run
  yourself afterwards.

## Report

```
Behaviour: <one sentence>
Red: <test name>, <command> -> failed with <assertion>
Green: <command> -> <n> passed, suite <n> passed
Refactor: <what changed, or "nothing">
```
