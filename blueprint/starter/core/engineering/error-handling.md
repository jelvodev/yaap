---
sources: [wshobson/plugins/developer-essentials/skills/error-handling-patterns, own]
category: error-handling
summary: Catch only what you expect, fail loudly at the boundaries, and write errors that say what to do.
rules:
- Catch only what you expect and can handle; a swallowed error or a default that looks like success hides bugs.
- Catch broadly only at a boundary that must keep running, and log the error there with its context, so it can be
  traced.
- Say in an error message what failed and what to do next, so the reader can act.
---

# Error handling

## What to catch

Catch an exception where you can do something useful with it: retry, fall back, or turn it into a clear message.
Name the exact types (`FileNotFoundError`, a timeout, a validation error). Everything else should travel up to a
boundary.

A boundary is a place that must survive a failure of what it runs: an HTTP handler, a background job, a loop that
processes items one by one, a plugin host. Only there catch broadly, and then always record the error with enough
context to find it (which item, which request), and say in a comment why the broad catch is needed.

## Failing well

- **Fail early.** Check input where it enters (an API, a file, a command-line argument) and refuse it with a clear
  message, instead of letting a bad value fail deep inside.
- **No silent defaults.** Returning an empty list or `None` after an error makes the caller believe nothing was there.
  Raise, or return a result that says it failed.
- **Clean up.** Use the language's construct for it (`with`, `try/finally`, `using`, `defer`) so files, locks and
  connections are released on the error path too.
- **Keep the cause.** When you wrap an exception, chain the original (`raise … from error` in Python, `cause` in
  JavaScript), so the trace still shows where it started.

## Messages

A good message answers three questions: what failed, why (if known), and what the reader can do. "Could not read
`config.toml`: the file does not exist. Copy `config.example.toml` to start." is better than "Config error".

Messages shown to users say nothing about internals they cannot act on; the log keeps the details. Neither holds a
secret, a token or personal data (see the `security` standard).
