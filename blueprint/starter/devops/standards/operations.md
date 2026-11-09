---
sources: [wshobson/plugins/cicd-automation/skills/github-actions-templates, wshobson/plugins/cicd-automation/skills/secrets-management, own]
category: operations
summary: Infrastructure and pipelines as reviewed code, CI with least privilege, everything pinned, secrets only from
  a secret store, and every deploy reversible.
rules:
- Keep infrastructure, CI and deploy configuration as code in the repository; manual changes drift and cannot be
  repeated.
- Give CI jobs least privilege, with an explicit `permissions:` block and OIDC instead of long-lived cloud keys.
- Pin what you run (base images by digest, actions by full commit SHA, tools by version); a moving tag can change under
  you.
- Take secrets from a secret store or the CI's secret settings at run time, never from the repository, an image or a
  log.
- Know the rollback before you deploy, so a bad release costs minutes instead of an outage.
---

# Operations

How to build and change infrastructure, CI and deployments. The rules above are always in the prompt; this text
explains them. The skills `docker-images`, `github-actions`, `kubernetes-manifests`, `slos` and
`incident-response` hold the methods.

## Everything as code

Servers, clusters, DNS, CI workflows, deploy scripts and dashboards are files in the repository, changed through a
reviewed commit. A change made by hand in a console is invisible to the next person, is lost when the environment is
rebuilt and cannot be applied to a second environment. When something was changed by hand in an emergency, bring the
code in line afterwards.

Before applying infrastructure code, look at what it will change (`terraform plan`, `kubectl diff`, the tool's
equivalent) and read it. Applying is the user's decision.

## Least privilege in CI

A CI job runs code from the repository and its dependencies; anything it can reach, a compromised dependency can
reach too.

- Every workflow sets `permissions:` at the top, read-only by default, and a job adds only what it needs.
- A job that deploys to a cloud gets short-lived credentials through OIDC, scoped to this repository and branch or
  environment. A long-lived access key in a CI secret is valid until someone remembers to rotate it.
- Deploys to production run in a protected environment that needs an approval or a protected branch.
- Untrusted input (pull request titles, branch names) never goes straight into a shell line; pass it through an
  environment variable.

## Pin what you run

A tag like `latest`, `v4` or `3.13-slim` points somewhere else tomorrow. Pin:

- base images by digest (`python:3.13-slim@sha256:…`), with the tag beside it for readers;
- third-party actions by full commit SHA, with the version in a comment;
- tools and providers by exact version, with their lock files committed.

Let an update bot propose the new digests and SHAs, so pins stay current and every update is a reviewed change.

## Secrets

Secrets live in a secret store (a vault, the cloud's secret manager, the CI's secret settings) and reach the
process at run time. They never go into the repository, an image layer, a build argument, a manifest in git or a
log line. Templates in the repository (`.env.example`) name the variables without values. When a secret did leak,
rotate it at its provider first; the core `leaked-secret` skill has the steps.

## Reversible deploys

Know before a deploy how to undo it: the previous image digest, the previous release, a feature flag. Database
changes that a rollback cannot undo are split so the old version still works (expand, then contract). Roll out in
steps where the platform allows it, and watch the service's SLOs while it rolls.

## In YAAP

- The agent writes infrastructure and CI files; it does not deploy, apply or push unless the user asks.
- YAAP checkpoints each change by itself. Undo a step of your own with git (`git restore <file>` through
  `run_command`), or ask the user to use *Restore to before this*.
- *Review these changes* gives CI and permission changes a second look by a model of another vendor.
- `/check` compares the project with these standards.
