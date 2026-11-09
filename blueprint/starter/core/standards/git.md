---
sources: [agency-agents/engineering/engineering-git-workflow-master.md, wshobson/plugins/git-pr-workflows/commands/git-workflow.md, own]
category: git
summary: Small commits with a clear subject, one task per branch, no rewriting of shared history, and nothing secret or
  generated in git.
rules:
- One change per commit with a short imperative subject; small commits are easy to review, revert and bisect.
- One task per branch, started from the latest main branch; two tasks in one branch make both hard to review.
- Never rewrite history others have pulled; it breaks their copies. Force-push only your own branch, with a lease.
- Keep secrets, build output, installed dependencies and local environment files out of git.
- Commit when the work is checked; push, merge or open a pull request only when the user asks.
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
history makes their copy diverge. If you must update your own pushed branch, use `--force-with-lease`, which refuses
when the remote has commits you have not seen. Never force-push the main branch.

When something goes wrong, `git reflog` still knows where every commit was; nothing committed is lost for a while.

## What stays out

- Secrets and populated environment files. Commit an example file with the variable names instead.
- Build output, caches, installed dependencies, editor and OS files: list them in `.gitignore`.
- Large binaries, unless the project uses a tool meant for them.

A secret that was committed is leaked even after a later commit removes it; follow the `security` standard.

## Who decides

Committing locally is part of finishing a checked step. Pushing, merging and opening a pull request reach other
people and are harder to take back, so they wait for the user. In YAAP the Changes tab shows the uncommitted work,
and a checkpoint can roll a step back before it is committed.
