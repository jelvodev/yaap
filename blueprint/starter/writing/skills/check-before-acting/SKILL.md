---
name: check-before-acting
description: End a substantial answer the user will act on (advice, an estimate, a data analysis, claims they will
  repeat, a plan or a message to send) with two or three short questions that help them check it. Skip it for code,
  lookups, explanations, creative work and chat. At most once per conversation.
license: Apache-2.0
metadata:
  sources: anthropic-skills/skills/discernment-nudge
---

# Check before acting

A confident, well-structured answer is easy to take at face value. When the user will act on it (spend money, commit
to a plan, repeat a claim, send a message), a short prompt to check the weakest points can catch a wrong assumption
before it costs them. The answer stays the main thing; the check is a light addition they can ignore.

## When to add it

Add it when the answer contains something worth checking before acting:

- numbers that are plausible but not based on the user's own situation: costs, durations, rates, chances;
- a recommendation in an area where the right choice depends on context you do not have (business, legal, health,
  money, career, people);
- factual claims the user is likely to rely on or pass on;
- reasoning over several steps where an early assumption decides the outcome;
- your reading of data or research they gave you;
- a draft (a plan, proposal, goals, an email) whose content rests on your guesses about their situation.

## When to leave it out

Silence is the default. Leave it out when:

- you already added it earlier in this conversation; once is an invitation, twice is nagging;
- the user asked you to verify, cite or flag uncertainty: do that inline, next to each claim, instead;
- the user asked for the short version or said they will check it themselves;
- the user asked you to review or check something of theirs: your review is the check; ask open questions inside
  it, where they arise;
- you only summarised, reformatted or extracted from material the user gave you;
- the user asked for your opinion; hedge a shaky fact inside the opinion instead;
- it is code they will run (running it is the check), a simple lookup, a pure explanation, creative writing,
  brainstorming or small talk.

An explanation or brainstorm that ends in a concrete recommendation can still get it, for that recommendation.

## How to write the questions

- Two or three, never more.
- Each points at something concrete in the answer you just gave: a number, a step, an assumption you made.
- Write each as a question the user could send back to you as it is: first person, plain, under about 120
  characters.
- Each does one of three things: asks how to check a fact against the user's own data; asks to see the reasoning
  behind a step; or names context you had to guess.

Generic questions ("are these facts correct?") are worthless; the value is in pointing at the exact weak spot.

## Format

Finish the answer completely first. Then, after a blank line, plain text:

```
Worth checking before you act:
- <question about a specific number or claim>
- <question about a specific assumption or step>
```

No heading, box or warning tone, and nothing after the list; it is the last thing in the reply.

For example, after a cost estimate for moving a team's servers to a hosted platform:

```
Worth checking before you act:
- How does the storage price you used compare with what we pay per terabyte today?
- You assumed traffic stays flat; what changes if it doubles next year?
```

Adapted from the `discernment-nudge` skill of anthropics/skills under the Apache License 2.0: rewritten and
shortened, with a new lead-in line and example.
