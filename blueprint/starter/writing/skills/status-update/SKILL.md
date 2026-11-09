---
name: status-update
description: Write a status update, project report, incident summary or set of FAQ answers that a team, a manager or
  stakeholders read quickly. Use this whenever the user asks for a weekly update, a progress report, a summary of an
  outage or incident, an announcement of a change, or answers to questions people keep asking, even when they just
  paste notes and say "turn this into an update", because these texts have a known shape and readers scan them for
  the same few things.
license: Apache-2.0
metadata:
  sources: anthropic-skills/skills/internal-comms
---

# Status updates and summaries

People read updates in passing. They look for three things: is it fine, what changed, and do I need to do anything.
Give them those first, in a fixed shape, with facts instead of adjectives.

## 1. Settle the frame

- **Kind.** A status update, an incident summary, FAQ answers, or another message (an announcement, a change
  notice). Pick the shape below; for anything else, use the general rules at the end.
- **Reader.** Who reads it and how much they know. A team needs detail; leadership needs the outcome and the ask.
- **Period.** The dates or the event it covers.
- **Material.** Use what the user gives you: notes, commits (`git_diff`, the log), issue lists, attachments, earlier
  updates. Ask for what is missing rather than inventing it; never fill a gap with a plausible number.

## 2. Write it in its shape

### Status update

```
<Project or team>, <period>
Status: <on track | at risk | off track>, <one sentence why>

Done
- <outcome, with a number or link where there is one>

Next
- <what comes next, with an owner and date where it matters>

Needs attention
- <blocker, risk or decision needed, and from whom; or "nothing">
```

- Three to five bullets per part at most. Group small items into one.
- Write outcomes, not activity: "the export runs in 2 minutes instead of 20", not "worked on export performance".
- "At risk" and "off track" always name what would bring it back and who must act.
- Keep the same shape week after week, so readers can compare.

### Incident summary

```
<Title>, <date>
Impact: <who or what was affected, how badly, for how long>
Status: <resolved | mitigated | ongoing>

Timeline (<time zone>)
- <time>: <what happened or was done>

Cause: <the root cause, or what is known so far>
Fix: <what stopped it, and what makes it hold>
Follow-up
- <action, owner, date>
```

- State the impact in the reader's terms (users who could not log in, orders delayed), with numbers.
- Blameless: describe what the system and process allowed, not who made the mistake.
- Keep "known" and "suspected" apart. An unconfirmed cause is labelled as such.

### FAQ answers

```
Q: <the question as people ask it, one sentence>
A: <the answer in one to three sentences> (<source>)
```

- Collect the questions from the material; merge ones that ask the same thing.
- Order by how many people it affects, not by when it was asked.
- Base each answer on a source the reader can open, and link it. When the answer is not settled, say so and who
  decides.

## 3. General rules for any update

- The most important line comes first; a reader may stop after it.
- Short sentences, active voice, no hype. "Shipped" and "slipped" are both fine words.
- Numbers over adjectives; a link over a paraphrase.
- Every request names who must act and by when.
- Match the tone the user's organisation uses when the user shows an earlier example; otherwise plain and neutral.

## 4. Check before handing it over

- Read it as the busiest reader: could they act on the first three lines alone?
- Check every number, date and name against the material. Mark anything you could not check.
- Cut what the reader does not need; link to the detail instead.

Give the user the text ready to paste, and list separately anything you assumed or could not check.

Adapted from the `internal-comms` skill of anthropics/skills under the Apache License 2.0: rewritten, its
company-specific formats and tools left out, and the status, incident and FAQ shapes written new.
