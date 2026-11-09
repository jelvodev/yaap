---
sources: [agency-agents/engineering/engineering-technical-writer.md, own]
category: documentation
summary: Docs change with the code, comments say why, and a README tells a newcomer how to run and test the project.
rules:
- Update the docs in the same change as the code; a wrong doc costs more than a missing one.
- Comments say why, not what; a comment that repeats the code goes stale.
- A README says what the project is and how to run and test it, with commands you ran, because readers paste them.
---

# Documentation

What to write down and how. The rules above are always in the prompt; this text explains them.

## Docs move with the code

When a change alters how something is used (a new option, a renamed command, a different default, a removed
endpoint), the doc that describes it changes in the same commit. Search the docs for the old name before you
finish. A reader trusts a doc until it fails them once.

A decision that shaped the design (a framework choice, a data model, a trade-off) gets an architecture decision
record, so the reason survives the people who made it. The `architecture-decision` skill has the format.

## Comments

The code says what it does. A comment adds what the code cannot say: why this approach, which constraint forced it,
what would break if it changed, a link to the bug it works around.

- Good: `# The API returns pages of at most 100, even when asked for more.`
- Not needed: `# Loop over the users.`

Leave out history ("changed in the last release", "formerly called X"); that is what version control is for. Delete a
comment that no longer matches the code instead of leaving it to mislead.

## The README

A newcomer should be able to answer three questions in a minute: what is this, how do I run it, how do I run its
tests. In that order:

1. One or two sentences on what the project does and for whom.
2. What it needs (language version, services) and the commands to set it up and start it.
3. The command that runs the tests.
4. Where to go next: configuration, deeper docs, how to contribute.

Commands are copied into a terminal as they are, so they must work as written in a fresh checkout.

## Style

Write for someone who is new to the project: the point first, short sentences, one topic per section, the reader as
"you". Prefer an example to a paragraph of description, and keep examples short and correct. Use the project's terms
consistently; one thing has one name.

The `write-docs` skill covers longer pieces (guides, reference pages) and how to check them as a first-time reader.
