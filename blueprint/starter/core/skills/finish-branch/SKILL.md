---
name: finish-branch
description: Finish a branch by running the tests, then merging it, opening a pull request or keeping it, as the user
  chooses, and never discarding work unasked. Use it when a feature, fix or plan is complete, or the user says to wrap
  up, merge, open a pull request or finish.
license: MIT
metadata:
  sources: superpowers/skills/finishing-a-development-branch,
    wshobson/plugins/git-pr-workflows/commands/git-workflow.md
---

# Finish a branch

Prove the work is green, then let the user decide how it lands. The decision is theirs; the safety checks are yours.

## 1. Run the tests

Run the project's whole test suite, and its lint and build when it has them, on the tree as it is now. Every edit
since the last run could have broken something, so an older green result does not count.

If anything fails, stop here. Report the failures (the command and the failing tests) and fix them first, or ask the
user whether to. The options below come after a green run.

## 2. Look at the state

- `git_status`: are there uncommitted changes? Ask whether to commit them with `git_commit`, or what else to do with
  them. Never finish with work left only in the working tree.
- Which branch is this, and which branch should it land on? Look in the plan, the conversation and the branch's
  upstream. When none of them settles it, ask a yes-or-no question ("This branch started from `main`, is that
  right?"), because a merge into the wrong branch drags unrelated commits along and is slow to untangle.
- Check that nothing secret or generated is in the commits (`.env` files, keys, build output).

## 3. Offer the options

Ask the user, in these words or close to them:

```
The tests pass (<command>: <result>). How should this branch land?

1. Merge it into <base> here
2. Push it and open a pull request
3. Keep the branch as it is for now
```

Then wait. Do not choose for the user, and do not add "discard" as an option.

In goal mode, do not merge or push by yourself: end with `finish_goal` (`done`), with these options in the summary.

## 4. Carry out the choice

### Merge into the base

1. Switch to the base branch and bring it up to date with its remote.
2. Merge the branch (`git merge <branch>` with `run_command`).
3. Run the tests again **on the merged result**. Two green branches can still break each other.
4. If they fail, stop. The merge is local and nothing was pushed; report it and investigate with the
   `systematic-debugging` skill.
5. When it is green, delete the branch with `git branch -d <branch>`, which refuses when commits would be lost. Push
   the base only when the user asks.

### Open a pull request

1. Push the branch with `git_push`.
2. Open the pull request against the base with `create_pull_request`. Follow the project's pull request template if
   it has one; otherwise use the template below.
3. Report the link. Keep the branch: review comments get fixed on it (see the `receive-review` skill). The rail's
   *Branches and pull requests* panel shows its checks.

### Keep the branch

Report the branch name and its last commit, and what is still open. Nothing else changes.

## Never discard work unasked

Delete work only on an explicit request from the user, never as a tidy-up you thought of yourself. Even then, first
show what would be gone (the branch, the subjects of its commits, the uncommitted files) and wait for a clear yes.
This covers `git branch -D`, `git reset --hard`, `git clean`, *Discard all…* under Changes, and deleting a worktree.

- A push that is rejected means the remote moved. Fetch and look at what changed; never force-push unless the user
  asks for exactly that.
- A tool that refuses because of uncommitted or unmerged work is protecting something. Show the user what it is
  instead of overriding the refusal.

## Pull request template

```
## What and why
<what changed and the reason, in a few lines; link the issue or plan>

## How it was tested
- `<command>`: <result>
- <manual checks, for example the flow clicked through in the preview>

## Notes for the reviewer
- <breaking changes, migrations, new settings, or "none">
- Not verified: <what could not be checked, or "nothing">
```

## Report

```
Tests: <command> -> <result>
Choice: merged into <base> | pull request <link> | kept <branch>
Result: <merge commit and the test run on it, the pushed branch, or the branch's last commit>
Not verified: <..., or "nothing">
```
