---
sources: [agency-agents/security/security-secrets-credential-engineer.md, agency-agents/security/security-architect.md, own]
category: security
summary: Keep secrets out of everything you write, distrust input, grant the least access that works, and pin what you
  depend on.
rules:
- Keep secrets out of code, logs, commits and tool output; read them from the environment, so the repository grants
  no access.
- Validate input where it enters and pass it to queries and commands as parameters; joined strings invite injection.
- Give code, tokens and services the least access that works; what cannot act cannot be abused.
- Pin dependencies with a lock file, and check a new one's name, upkeep and need before adding it.
- A leaked secret is rotated at the provider first, then removed from the code; deleting it does not take it back.
---

# Security

The few habits that prevent most incidents. The rules above are always in the prompt; this text explains them.

## Secrets

A secret is anything that grants access: an API key, a password, a token, a private key, a connection string with a
password in it. Keep it in the environment or a secret store and read it at run time. The repository holds only the
name of the variable, documented in an example file (`.env.example`) without a value.

Secrets also leak through the side door: a log line that prints the request headers, an error that includes the
connection string, a test fixture with a real key, a variable with a public prefix that ends up in a browser bundle.
When you show a secret at all, show its kind and at most its last few characters.

## Input

Treat everything from outside as hostile until checked: request bodies, query strings, headers, uploaded files, file
names, environment values, the output of another program. Check it where it enters, against what is allowed (an
allow list, a type, a range), and refuse the rest with a clear message.

Never build a query, a shell command, a path or HTML by joining strings with input in them. Use parameters for
queries, an argument list instead of a shell string for commands, a resolved path checked against its base folder
for files, and the template's escaping for HTML.

## Least access

Each token, database user, service account and container gets only what its job needs, and one credential serves
one purpose, so revoking it breaks one thing. Prefer short-lived credentials where the platform offers them. Check
access on the server for every request; a hidden button is not a permission check.

Do not turn off a security control (certificate checks, CSRF protection, a sandbox) to make something work. Find out
why it fails.

## Dependencies

Every dependency is code you run without reading it. Before adding one, check that the name is spelled right (a
typo can be a lookalike package), that it is maintained, and that the standard library or an existing dependency
does not already do the job. Add it with the project's package manager so the lock file records the exact version,
and commit the lock file.

## When a secret leaked

Assume it was used from the moment it was committed, pushed or logged. Rotate or revoke it at the provider first;
only then remove it from the code and, if needed, from the history. Then check the provider's logs for use during
the exposure. The `leaked-secret` skill has the steps; the `threat-model` skill helps when a design needs a wider
look.
