---
name: incident-response
description: Run an incident from the first alert to a blameless postmortem, with a severity, clear roles, regular
  updates and mitigation before root cause. Use it whenever a service is down, slow or losing data, an alert fires, a
  deploy broke production, or the user asks for a postmortem or runbook.
license: MIT
metadata:
  sources: agency-agents/engineering/engineering-incident-response-commander.md
---

# Incident response

During an incident the goal is to stop the harm, not to understand it. Understanding comes after, in the
postmortem. Work in this order: declare, stabilise, communicate, resolve, learn.

## 1. Declare and classify

Say that it is an incident, give it a short name and a severity. The severity decides who is involved and how often
you update; set it from the impact, and raise it as soon as the impact grows.

| Level | Impact | Response | Updates |
|---|---|---|---|
| SEV1 | Service down for most users, data lost or corrupted, or a security breach | Everyone needed, now | Every 15 to 30 minutes |
| SEV2 | A core feature down or badly degraded for many users; no workaround | The owning team, now | Every 30 to 60 minutes |
| SEV3 | A minor feature broken, or a workaround exists | The owning team, within working hours | Daily |
| SEV4 | No user impact yet: a warning, a near miss | Planned work | When it changes |

Any doubt about data integrity or security makes it SEV1 until shown otherwise.

## 2. Assign roles

Even with two people, name them:

- **Incident lead**: coordinates, decides, keeps the timeline. Does not debug; someone has to see the whole picture.
- **Operations**: investigates and changes the system, and says what they do before doing it.
- **Communications**: writes the updates for users and stakeholders (in a small team, the lead does this).

## 3. Stabilise first

Prefer the action that ends the impact fastest and is easiest to undo:

1. Roll back the last deploy or configuration change, when the timing fits. Most incidents follow a change.
2. Turn off the feature behind a flag.
3. Shift or shed load: scale out, rate limit, fail over.
4. Restart only when you have captured what you need to understand it later (logs, a heap dump).

Fix forward only when a rollback is impossible or riskier. Give each hypothesis a time box (15 minutes); when it is
not confirmed by then, try the next one.

## 4. Keep a timeline

Write down, with times in UTC: what was seen, what was decided, what was changed, by whom and what happened next.
Memory is unreliable an hour later; the timeline is the source for the postmortem.

## 5. Communicate on a schedule

Send updates at the interval of the severity, also when nothing changed. Each update says:

```
Status: <investigating / mitigated / resolved>
Impact: <who is affected and how, in user terms>
Now: <what is being done>
Next update: <time>
```

Say what users can do (a workaround) and avoid guesses about the cause.

## 6. Resolve

The incident is resolved when the impact is gone and the signals that showed it (error rate, latency, the SLO) are
back to normal for a while. Then say so, with the duration and what is still to do.

## 7. Postmortem

Write one for every SEV1 and SEV2, within a few working days, while memories are fresh. It is blameless: it asks what
in the system allowed the failure (a missing test, alert, limit or guard), not who made a mistake. People who fear
blame hide what they know.

```
# Postmortem: <incident name>

Summary: <two or three sentences: what happened, impact, how it ended>
Impact: <users affected, duration, data affected, SLO budget used>
Timeline: <UTC times from first signal to resolution>
Cause: <the chain of causes; ask "why" until you reach something the system can change>
Detection: <how we found out, and how long it took>
What went well: <…>
What went badly: <…>
Actions: <each with an owner and a date; prevent, detect sooner, or recover faster>
```

Track the actions like other work. A postmortem whose actions are never done will be written again.

## In YAAP

- The agent helps with the timeline, the updates, reading logs and metrics, and the postmortem. It changes
  production only when the user asks for that specific action, and says first what it will do and how to undo it.
- Read logs and command output with the tools you have; quote what you saw with its time.
- After the postmortem, record the failure mode and its runbook in the project memory with `update_memory`, so the
  next incident of the same kind starts from it.
