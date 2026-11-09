---
name: onboard-codebase
description: Map an unfamiliar repository from the files actually read (what it is, entry points, how to run and test
  it, conventions, risks) and propose lines for the project memory. Use it when starting in a project new to you, when
  asked to explain or map a codebase, or while its memory is empty.
license: MIT
metadata:
  sources: agency-agents/engineering/engineering-codebase-onboarding-engineer.md, own
---

# Onboard a codebase

A wrong picture of a project causes more bad changes than any lack of skill. Build the picture from files you have
read, say which ones those were, and keep what lasts in the project memory so the next conversation starts from it.

## Ground rules

- **Only state what you read.** Every claim points to a file. "Requests are routed in `src/api/router.ts`" is a fact;
  "probably uses a service layer" is a guess, and a guess in an onboarding map gets repeated as truth.
- **Say what you did not read.** A map of one subsystem is not a map of the repository. List the folders you skipped.
- **Describe, do not review.** Onboarding is not the moment for refactoring ideas. Risks are noted as facts (no tests
  in `billing/`), not as plans.
- **Read only.** Change no files while onboarding, apart from the memory lines the user accepts at the end.

## 1. Inventory

Start from the repository map YAAP gives at the top of a project conversation; it shows folders, modules and public
names, but check it against the files before relying on it. Then read, in this order:

1. `README`, `CONTRIBUTING`, `AGENTS.md` or similar instructions, and `docs/` indexes.
2. Manifests and lockfiles (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `pom.xml`): language, framework,
   scripts, dependencies.
3. Build, CI and deployment files (`Makefile`, `Dockerfile`, `.github/workflows/`, `compose` files): how it is really
   built and tested, which often differs from the README.
4. Tool configuration (linters, formatters, type checkers, test runners): the conventions that are enforced.

Decide what kind of repository it is: an application, a library, a service, a monorepo with several of these, or a
mix.

## 2. Entry points and paths

Find the smallest set of files where the system starts: `main` functions, the server start, the router, CLI
commands, workers and scheduled jobs, or a library's public exports. Then trace one or two real paths end to end,
for example one request from the route to the database and back. Name each file on the way. A traced path teaches
more than a list of folders.

For a large repository, hand each area to an exploring worker with `delegate` (it only reads) and ask for files,
lines and a conclusion. Your own context stays small, and the workers' reports are evidence you can cite.

## 3. Run and test

Find the commands for install, build, run and test in the scripts, the CI file and the README. Where the project
allows it, run the test command once and note the result: a suite that is already red, slow or needs a service is
the most useful thing a newcomer can learn. Running commands asks for approval; if the user declines, list the
commands as found, not as verified.

For a web app, start it with `start_process` with its port and look at it in the preview beside the chat.

## 4. Conventions

Write down what the code and its configuration show: naming, layout of a feature, how errors are handled, how tests
are named and where they live, the commit style (`git log`), the formatter and line length. Prefer conventions that
are enforced (a lint rule, a CI check) over ones you infer from two files, and say which is which.

## 5. Risks

Note, as facts with their file: areas without tests, generated code that must not be edited by hand, very large
modules, duplicated helpers, misleading names, configuration that holds or expects secrets, dependencies that are far
behind, and anything the README says that the code contradicts.

## 6. Propose memory lines

The project memory (`.blueprint/memory.md`) is seen by every conversation in the project. Propose a few short lines
for its sections (*Decisions*, *Conventions*, *Lessons learned*, *Notes*): facts that will matter in later work and
are not obvious from the repository map. Good: "Tests run with `uv run pytest` from `backend/`; the e2e suite needs
the dev server on port 5173." Not useful: "The project uses Python."

Show the lines and ask which to keep. Write only the accepted ones, one `update_memory` call each.

## Output

```
# <project name>

<One sentence: what this codebase is.>

## In five minutes
- Kind: <application / library / service / monorepo>, <languages and frameworks>
- Entry points: `<path>` (<why it matters>), ...
- A traced path: `<entry>` -> `<handler>` -> `<logic>` -> `<storage or output>`
- Read these three files first: `<path>`, `<path>`, `<path>`

## Structure
| Path | What it holds |
|---|---|
| `<path>` | <purpose> |

## Run and test
- Build: `<command>` (<verified / found in <file>>)
- Run: `<command>`
- Test: `<command>` (<result if run>)

## Conventions
- <convention> (<enforced by <tool> / seen in <files>>)

## Risks
- <fact> (`<path>`)

## Proposed memory lines
- <Section>: <line>

## Read / not read
- Read: <files and folders>
- Not read: <folders skipped>
```
