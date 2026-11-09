---
name: refactor-safely
description: Restructure code without changing what it does - rename, extract, move, split a large module, remove
  duplication or dead code - in small steps under passing tests, with a checkpoint before each step. Use this
  whenever the task is a refactor, a cleanup, a reorganisation or "make this easier to read", and whenever a feature
  or fix first needs the code reshaped, so the reshaping and the behaviour change stay apart.
license: MIT
metadata:
  sources: wshobson/plugins/codebase-cleanup/commands/refactor-clean.md, own
---

# Refactor safely

A refactor changes the shape of the code and nothing else. Once behaviour changes too, a failing test can no longer
tell you which of the two went wrong. So keep every step small enough that the tests prove it changed nothing, and
undo a step instead of debugging it.

## 1. Pin the behaviour

Run the tests for the code you will touch, and the build or type check, before any change. They must be green;
refactoring on red hides new breakage among the old. Write down the command and the result.

If the code has few or no tests, write characterisation tests first: tests that record what the code does now,
odd results included, through its public interface. They are the safety net. A refactor without one is a rewrite
with hope.

## 2. Plan the steps

Name the target shape, then the sequence of steps that gets there. Each step is one mechanical move that leaves
the code working:

- rename a function, variable, class or file;
- extract a function, a class or a module, or inline one that adds nothing;
- move code to where it belongs, keeping the old import working for now if others use it;
- replace a duplicate with a call to the one that stays;
- remove code that `find_references` shows nobody uses.

For a refactor over more than a few files, agree on the steps with the user first (the `write-plan` skill).

## 3. One step at a time

For each step:

1. **Checkpoint.** YAAP snapshots the project before each edit, so *Restore to before this* on the step's first
   edit undoes the whole step. In a git project, a commit after each green step is a checkpoint that lasts.
2. **Make the one change.** Use `rename_symbol` for renames, so every reference changes at once; use
   `find_references` before moving or deleting anything.
3. **Run the tests and the type check.** Read the output.
4. **Green: go on. Red: undo.** Restore the checkpoint and take a smaller step. Fixing forward mixes a refactor with
   a repair, and the step loses its proof.

## Keep behaviour out

- No new features, no bug fixes, no changed error messages inside a refactor. A bug you find gets a note, and later
  its own change with a test that shows it.
- Do not change tests to make them pass, except where a test reaches into structure you moved (an import path, a
  private name). Then change only that, and say so.
- Keep public interfaces stable. When a public name must change, keep the old one as a thin alias, mark it
  deprecated, and remove it in a later change.
- Leave formatting of untouched lines alone; it hides the real change in the diff.

## 4. Finish

Run the full test suite, the build, the linter and the type checker. Then read the whole diff as a reviewer: every
hunk should be a move, a rename or a removal. Anything that reads as different behaviour is either reverted or
called out. When the project has standards, `/check` shows whether the new shape follows them.

## Report

```
Goal: <the target shape in one sentence>
Steps: <n>, each green before the next
- <step>: <files>
Behaviour: unchanged; <test command> before: <result>, after: <result>
Public interface: <unchanged / <name> kept as deprecated alias>
Found, not fixed: <bugs or smells noted for a separate change, or "nothing">
Not verified: <what could not be checked, or "nothing">
```
