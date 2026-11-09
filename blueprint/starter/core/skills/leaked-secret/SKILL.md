---
name: leaked-secret
description: Respond to a leaked secret - an API key, token, password, private key or connection string that ended
  up in a git commit, a pushed branch, a log, a screenshot, an issue or a chat. Rotate it at the provider first, then
  remove it, then check whether it was used, and close the gap that let it out. Use this the moment a secret is found
  where it should not be, when a secret scanner or the hosting service reports one, or when the user says they pasted
  or committed a key by mistake, even if the commit was never pushed or was already deleted.
license: MIT
metadata:
  sources: agency-agents/security/security-secrets-credential-engineer.md
---

# Leaked secret

A secret that left its store is burned from the moment it left, not from the moment someone noticed. Deleting the
commit or the message does not take it back: clones, forks, caches, CI logs, backups and the systems a chat went
through still hold it. Only revoking it at the provider ends the exposure. So the order is fixed: **rotate, remove,
check, prevent.**

## Never repeat the value

Do not print, quote, search for or paste the secret in the conversation, a command, a commit message or a file.
Refer to it by what it is and where it was found ("the payment provider's live key in `config/settings.py`, line 12,
commit `3f2a1bc`"), and at most its last four characters. A command that greps for the value puts it in your output
and the shell history; search for the variable name or the file instead.

## 1. Identify it

Find out, without echoing it:

- What it is: which provider or system, which account, which environment (test or live).
- What it can do: read only, write, admin, billing. This sets the urgency.
- Where it went: a local commit only, a pushed branch, a public repository, a CI log, a ticket, a chat, a
  screenshot. And since when: the commit time or the time it was posted.
- Who owns it and who else uses it, so rotation does not break a service unannounced.

A key designed to be public (a publishable key, a client id) is not a leak. Say so and stop.

## 2. Rotate at the provider first

This is the fix; everything else is cleanup. The agent cannot do it: tell the user exactly where to go (the
provider's console or CLI, the key's name) and what to do.

- Create a new credential with the narrowest rights that still work.
- Put the new value where it belongs, never in the chat or the code: in YAAP's vault, where the agent uses it by name
  without seeing it, or in the deployment's secret store.
- Update everything that uses it, then revoke the old one. When a live service depends on it, keep the overlap short:
  minutes, not days. When the key is powerful and was public, revoke first and accept the outage.

Do not move on until the user confirms the old value is revoked.

## 3. Remove it where it leaked

- **Code.** Replace the value with a reference to the secret store or an environment variable, add the file to
  `.gitignore` if it is a local config such as `.env`, and document the variable in `.env.example` without a value.
  Commit that as its own change.
- **Git history.** Once the secret is revoked, rewriting history is optional hygiene, not the fix. If the user wants
  it, rewrite with a history tool such as `git filter-repo` on a fresh clone, then force-push, which needs everyone
  with a clone to re-clone. On a public host the old commits can live on in forks and cached pull requests; the
  host's support can purge those. Ask before any force-push; it is the user's decision.
- **Logs and tickets.** Delete or redact the entries where the system allows it, and check where those logs were
  shipped (a log service, an error tracker).
- **A chat.** A secret pasted into a conversation has also reached the model provider and YAAP's database and
  backups. Rotation is still the fix; deleting the conversation afterwards only tidies up.

## 4. Check whether it was used

Look at the provider's access or audit log, and its billing, for the window from the first exposure to the
revocation: calls from unknown addresses, regions or user agents, new resources, changed settings, data exports. The
user has to open these; tell them what to look for.

If anything looks like use by someone else, this is now an incident: find out what that credential could reach,
check those systems too, and follow the organisation's incident process.

## 5. Prevent the next one

- A secret scanner in a pre-commit hook and in CI, so a key fails the commit before it is pushed.
- Secrets read from the environment or a secret store, never from tracked files; `.env` ignored, `.env.example`
  tracked.
- Short-lived or narrowly scoped credentials where the provider offers them, so the next leak is worth less.

## Report

```
Secret: <kind, provider, environment; last four characters at most>
Exposed: <where> since <time>; <local only / pushed / public>
Rotated: <yes, by the user at <time> / waiting for the user>
Removed: <code change, history rewrite or not, logs>
Use found: <none in <logs checked> for <window> / suspicious: <what>>
Prevention: <what was added, or proposed>
Open: <what still needs the user>
```
