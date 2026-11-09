---
sources: [agency-agents/engineering/engineering-git-workflow-master.md, wshobson/plugins/git-pr-workflows/commands/git-workflow.md, own]
category: git
summary: Small commits with a clear subject, one task per branch, no rewriting of shared history, and commits only
  when the user asks.
rules:
- One change per commit with a short imperative subject; small commits are easy to review, revert and bisect.
- One task per branch, from the latest main, so each can be reviewed alone.
- Never rewrite history others have pulled; it breaks their copies.
- Commit when the user asks, or per step when the user approved a plan that says so; never push or merge unasked, since
  that reaches others.
---

# Git

How changes go into version control. The rules above are always in the prompt; this text explains them.

## Commits

A commit is one change that makes sense on its own: a fix with its test, a feature step, a rename. If the message
needs "and", it is probably two commits. Each commit should leave the project working, so that any commit can be
checked out, reverted or found with `git bisect`.

The message:

- a subject line in the imperative, under about 70 characters, without a full stop: "Fix the date parsing after
  midnight", not "fixed stuff" or "WIP";
- a blank line, then a body when the reason is not obvious: why the change was needed and what it affects. The diff
  already shows what changed.

Follow the project's own convention when it has one (a prefix such as `fix:`, a ticket number). Look at the recent
log before the first commit.

## Branches

Start a branch from the latest main branch, name it after the task (`fix/login-redirect`), and keep it short-lived.
Bring in changes from main before you finish, so conflicts are solved on the branch and not on main. The
`finish-branch` skill covers what happens at the end: tests, then merge, a pull request or keep.

## History

Rebasing, amending and squashing are fine on a branch only you use. Once someone else has pulled it, rewriting its
history makes their copy diverge. Force-push only your own branch, and then with `--force-with-lease`, which
refuses when the remote has commits you have not seen. Never force-push the main branch.

When something goes wrong, `git reflog` still knows where every commit was; nothing committed is lost for a while.

## What stays out

- Secrets and populated environment files. Commit an example file with the variable names instead.
- Build output, caches, installed dependencies, editor and OS files: list them in `.gitignore`.
- Large binaries, unless the project uses a tool meant for them.

A secret that was committed is leaked even after a later commit removes it; the `security` standard has the rule
and the `leaked-secret` skill the steps.

## Who decides

Commit when the user asks, or per step when the user approved a plan that says so; otherwise a checked step stays
uncommitted for the user to look at. Pushing, merging and opening a pull request reach other people and are harder
to take back, so they always wait for the user. In YAAP the Changes tab shows the uncommitted work, and YAAP takes a
checkpoint by itself before every change, so the user can roll a step back with *Restore to before this*.
