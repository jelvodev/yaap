---
name: write-docs
description: Write or revise developer docs (a README, guide, how-to, reference or design explanation) by gathering
  context, agreeing on an outline, writing section by section and testing it on a newcomer. Use it when asked for
  docs, when a change alters what users must know, or when docs are stale.
license: MIT
metadata:
  sources: agency-agents/engineering/engineering-technical-writer.md, own
---

# Write docs

Documentation fails in two ways: it is wrong, or it assumes what the reader does not know. Both come from writing
before understanding the reader and the code. Gather first, outline second, write third, and then test the result
on someone who knows nothing.

## 1. Pick the kind

Each document does one job. Mixing them makes all of them worse.

| Kind | The reader wants to | Shape |
|---|---|---|
| Tutorial | learn by doing, from zero | numbered steps to one working result |
| How-to guide | get one task done | goal, prerequisites, steps, how to check it worked |
| Reference | look up a fact | complete, consistent entries; no story |
| Explanation | understand why | prose about design, trade-offs and context |

A README is a front door: what this is, why it exists, the shortest way to start, and links to the rest.

## 2. Gather context

- **The reader.** Who reads it, what they already know, and what they want to do after reading. Ask the user when
  this is unclear; it decides every later choice.
- **The facts.** Read the code, the configuration, the existing docs and the tests. Run the commands the document
  will tell readers to run, in a clean state where you can. A step you did not run is a step that may be wrong.
- **The gaps.** Look at issues, questions and review comments about the topic: they show where readers get stuck.
- **The rules.** If the project has a documentation standard, load it with `load_standard` and follow it; follow the
  tone and layout of the docs next to the new one.

Ask the user a few short questions at once rather than one per reply, and say what you will assume if they skip
them.

## 3. Outline first

Write the headings and one line per heading on what it says. Show the outline for anything longer than a page and
agree on it; moving a heading is cheap, rewriting three sections is not. Check that each section has one purpose and
that the order follows what the reader does.

## 4. Write section by section

- The point first: the first sentence of a section says what it is for or what to do.
- Second person, present tense, active voice: "Run `make test`", not "The tests can be run".
- One concept per section; installation, configuration and usage are separate.
- Define each term at its first use, or link to where it is defined.
- Examples are short, complete and correct: commands with their expected output, code that runs as shown. Check
  each one.
- Say what can go wrong at the step where it happens, with the message the reader will see and what to do.
- Link instead of repeating. Two copies of a fact drift apart.

For a document the user wants to shape with you, open it beside the chat with `open_document`: they see each change
and their edits reach you.

## 5. Read it as a newcomer

Re-read the whole document as a reader who has only this page and the project. Better: hand it to an exploring
worker with `delegate`, give it only the document and the task it describes, and ask where it got stuck, what it had
to guess and which terms it did not know. Fix those, then check:

- Every command and example was run or checked against the code.
- No step depends on something only the author knows (a local path, a setting, an earlier conversation).
- The first screen of a README answers what it is, why it matters and how to start.
- Links work; names match the code exactly.

## 6. Keep it true

Docs go in the same change as the code they describe, so they cannot fall behind. When a change breaks how readers
use something, the docs say what to do instead (and the release notes do too, see the `release-notes` skill).

## README outline

```markdown
# <Name>

<One sentence: what it is and who it is for.>

## Why
<Two or three sentences: the problem it solves.>

## Quick start
<The shortest path to something working, with the expected result.>

## Install
<Prerequisites with versions, then the steps.>

## Use
<The most common task, fully worked; then the next most common.>

## Configure
<Settings that matter, with defaults; link to the full reference.>

## Develop
<Run, test and contribute, or a link to CONTRIBUTING.>

## License
```
