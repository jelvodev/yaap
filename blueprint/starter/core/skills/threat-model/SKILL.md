---
name: threat-model
description: Threat-model a change or a system with STRIDE over its trust boundaries, rating each threat and pairing
  it with a mitigation. Use it before building or reviewing anything with authentication, permissions, secrets,
  payments, personal data, uploads, webhooks or outside input.
license: MIT
metadata:
  sources: agency-agents/security/security-architect.md,
    wshobson/plugins/security-scanning/skills/stride-analysis-patterns
---

# Threat model

Most security bugs sit where data crosses from a place you trust less into one you trust more. Find those
crossings first, then ask the same six questions at each one. That is cheaper before the code exists than after.

## 1. Scope

Say what is modelled: one change (a new endpoint, a new integration) or a whole system. For a change, model only
what it adds or alters, plus the boundaries it touches. Read the code or the design; for a large system, ask an
exploring worker (`delegate`) to list the entry points, data stores and outside calls with their files.

## 2. Draw the data flow

List, in words or a small diagram:

- **Actors:** users by role, administrators, other services, schedulers, anonymous visitors.
- **Processes:** the services, functions or jobs that handle data.
- **Data stores:** databases, caches, files, queues, logs, backups.
- **External systems:** third-party APIs, identity providers, payment providers, model providers.
- **Flows:** what moves between them, and over what (HTTP, a queue, a file, a shared folder).

## 3. Mark the trust boundaries

A trust boundary is where the level of trust changes: the internet to your server, a browser to your API, your
service to a third party, a user's role to an administrator's, your code to a plugin, a container to its host. Text
that reaches a model or an agent from outside (a web page, an uploaded file, an email) is untrusted input too, and so
is the model's output when it drives an action. Every flow that crosses a boundary gets its own row in step 4.

## 4. STRIDE per crossing

Ask each question at each crossing. Skipping a letter is how a class of threats is missed.

| Letter | Threat | Question | Typical controls |
|---|---|---|---|
| S | Spoofing | Can someone pretend to be another user or service? | Authentication, signed tokens, mutual TLS |
| T | Tampering | Can data be changed in transit, at rest or in the request? | Validation, integrity checks, signatures, least write access |
| R | Repudiation | Can someone deny an action because nothing proves it? | Audit log with actor and time, kept out of the actor's reach |
| I | Information disclosure | Can data reach someone who should not see it? | Authorization per object, encryption, no secrets in logs or errors |
| D | Denial of service | Can someone exhaust a resource or block others? | Rate limits, size limits, timeouts, quotas |
| E | Elevation of privilege | Can someone gain rights they were not given? | Server-side authorization, least privilege, sandboxing |

Write each threat as a concrete scenario: who does what, through which flow, to get what. "An authenticated user
changes the `account_id` in the request and reads another account's invoices" can be tested; "IDOR risk" cannot.

## 5. Rate severity

Use one scale for every threat, so the list can be sorted:

| Severity | Meaning |
|---|---|
| Critical | Remote code execution, full authentication bypass, or bulk access to secrets or personal data; exploitable now without special access. |
| High | Access to another user's data or actions, privilege escalation, or a leak of a credential; needs some access or effort. |
| Medium | Limited disclosure, an attack that needs an unlikely precondition, or a denial of service of one feature. |
| Low | Hardening gaps with little direct impact, such as verbose errors or a missing header on a non-sensitive page. |

When in doubt between two levels, pick the higher one and say why. Note the likelihood in the scenario rather than
lowering the severity in silence.

## 6. Mitigate

For each threat: the control that exists today (with its file), or the mitigation still needed, specific enough to
build and test ("check that the invoice's account belongs to the session user in `get_invoice`, with a test for a
foreign id"). Prefer fixing the cause at the boundary over a filter further in. Some threats are accepted; then say
who accepted them and why.

Critical and High threats without a mitigation block the change; say so plainly to the user.

## In YAAP

- The `security-reviewer` agent reviews code against the threats found here.
- Turn each needed mitigation into a test first (the `test-driven-development` skill), so it stays fixed.
- Record an accepted risk or a security design choice with the `architecture-decision` skill.

## Output

```
# Threat model: <change or system>

Scope: <what is in, what is out>
Read: <files and designs this is based on>

## Data flow
- Actors: <...>
- Processes: <...>
- Data stores: <...>
- External systems: <...>

## Trust boundaries
| # | Boundary | Crossing flows |
|---|---|---|
| B1 | <from> -> <to> | <flows> |

## Threats
| # | Boundary | STRIDE | Scenario | Severity | Existing control | Mitigation needed |
|---|---|---|---|---|---|---|
| T1 | B1 | S | <who does what to get what> | High | <control, file, or "none"> | <specific change and its test> |

## Accepted risks
- <threat>: accepted by <who>, because <why>

## Verdict
<Blocks the change / can go ahead after T1, T3 / no blocking threats>. Not covered: <what was not modelled>.
```
