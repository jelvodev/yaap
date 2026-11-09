---
name: github-actions
description: Write GitHub Actions workflows that run with least privilege, use OIDC instead of stored cloud keys, pin
  every action to a full commit SHA and cache dependencies. Use this whenever you create or change a workflow in
  .github/workflows, add a CI test, build, release or deploy job, a matrix or a reusable workflow, connect CI to a
  cloud account, or review a workflow for security, even for a small change, because a workflow runs with access to
  the repository and its secrets.
license: MIT
metadata:
  sources: wshobson/plugins/cicd-automation/skills/github-actions-templates, own
---

# GitHub Actions

A workflow runs third-party code with a token for your repository. Write it so that a compromised action or a
malicious pull request can do as little as possible, and so it is fast enough that people wait for it.

## You write it, the runner proves it

The agent cannot run a workflow. Write it, check it with `actionlint` when the project has it, run the same commands
locally with `run_command` (the tests, the build), and tell the user what to look for in the first run. Commit or
push only when the user asks.

## 1. Triggers

- `pull_request` for checks on proposed changes, `push` on the main branch for builds and deploys, `workflow_dispatch`
  for manual runs.
- Avoid `pull_request_target` and `workflow_run` with a checkout of the pull request's code: they run with the base
  repository's secrets and write token, so the pull request's code would get them too.
- Limit by `paths` when a workflow only concerns part of the repository.

## 2. Permissions

Set read-only permissions at the top of every workflow and widen them per job, only as far as that job needs.

```yaml
permissions:
  contents: read
```

Typical additions: `packages: write` to push an image to the registry, `id-token: write` for OIDC,
`pull-requests: write` to comment. Without the block, the token gets the repository's default, which can be write
access to everything.

## 3. Pin actions by commit SHA

A tag such as `v7` can be moved to other code at any time; a full commit SHA cannot. Pin every action, with the
version in a comment for readers, and let Dependabot or Renovate propose updates. Actions from your own repository
(`./.github/actions/...`) need no pin.

Look up the SHA of a release on the action's releases page, or with `git ls-remote --tags <action repository url>`.

## 4. A CI workflow

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  test:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3
        with:
          enable-cache: true
      - run: uv sync --locked
      - run: uv run pytest
```

- `persist-credentials: false` keeps the token out of the checkout's git configuration, where later steps could
  read it.
- `concurrency` cancels a pull request's older run when a new commit arrives, but lets runs on `main` finish.
- `timeout-minutes` stops a hung job long before the six-hour default.
- A fixed runner image (`ubuntu-24.04`) changes only when you change it; `ubuntu-latest` moves on its own.

## 5. Caching

Most setup actions cache the package manager's downloads, keyed by the lock file: `enable-cache` for `setup-uv`,
`cache: npm` or `cache: pnpm` for `setup-node`, `cache: pip` for `setup-python`. Use `actions/cache` directly only
for other tools, with a key that includes the lock file's hash (`hashFiles('**/package-lock.json')`). For image
builds, use the build action's cache (`cache-from: type=gha`, `cache-to: type=gha,mode=max`).

Never cache anything that holds a secret, and do not restore caches written by untrusted pull requests into a
release job.

## 6. Deploy with OIDC

Instead of a cloud access key stored as a secret, the job asks the cloud for short-lived credentials with an OIDC
token from GitHub. The cloud trusts only tokens for this repository and this environment.

```yaml
  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    environment: production
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: aws-actions/configure-aws-credentials@e1253824e5c10ff9df46874f81ed3ec929e19cfd # v6.3.0
        with:
          role-to-assume: arn:aws:iam::123456789012:role/deploy-orders-api
          aws-region: eu-west-1
      - run: ./scripts/deploy.sh
```

- In the cloud, the role's trust policy accepts only the subject `repo:<owner>/<repo>:environment:production`, so a
  workflow on another branch or repository cannot assume it. Other clouds work the same way with their own login
  action and a workload identity federation.
- `id-token: write` is granted on the deploy job only.
- The `production` environment gets protection rules in the repository settings (required reviewers or allowed
  branches); the user sets them there.

## 7. Secrets and untrusted input

- Pass a secret to a step through `env:` and let the tool read it; never `echo` it or put it on a command line.
- Expressions such as `${{ github.event.pull_request.title }}` are pasted into the script before it runs, so a
  crafted title becomes shell code. Pass them through an environment variable and quote it:

```yaml
      - run: echo "Title is $TITLE"
        env:
          TITLE: ${{ github.event.pull_request.title }}
```

- Secrets are not available to workflows triggered from forks, which is intended; do not work around it.

## 8. Matrices and reuse

- A matrix tests several versions or systems; keep it to what you support and set `fail-fast: false` when every
  result is useful.
- When several workflows repeat the same steps, move them to a reusable workflow (`workflow_call`) or a composite
  action in the repository, and pass secrets explicitly rather than with `secrets: inherit`.

## Checklist

- [ ] `permissions:` at the top, read-only; each job widens only what it needs
- [ ] Every third-party action pinned to a full SHA with a version comment
- [ ] Cloud access through OIDC, scoped to repository and environment; no stored cloud keys
- [ ] Caching keyed by the lock file
- [ ] `timeout-minutes` and `concurrency` set; a fixed runner image
- [ ] No untrusted expression directly in a `run:` line; no secret printed
- [ ] `persist-credentials: false` on checkout unless a later step pushes

## In YAAP

- *Review these changes* gives a workflow change a second look by a model of another vendor; permission and trigger
  changes deserve it.
- Record the project's CI layout and required checks in the project memory with `update_memory`.
