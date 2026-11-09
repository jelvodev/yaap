---
name: prompt-design
description: Design, improve and regression-test a prompt for a language model, such as a system prompt, an agent's
  role prompt, a Blueprint skill or command, an extraction or classification prompt, or a prompt inside an
  application. Use this whenever the user writes or changes a prompt, asks why a model keeps doing the wrong thing,
  wants more reliable or consistent output, or wants to know whether a prompt change made things better or worse,
  because prompts change behaviour in ways that are only visible when tested on fixed cases.
license: MIT
metadata:
  sources: agency-agents/engineering/engineering-prompt-engineer.md
---

# Prompt design

A prompt is a specification that a model reads instead of a person. Treat it like code: decide what correct output
looks like, test it on fixed cases, change one thing at a time, and keep it under version control.

## 1. Specify before you write

Write down, with the user:

- **The task** in one sentence, and who or what consumes the output (a person, a parser, another agent).
- **The output format** exactly: a JSON shape with its fields, a Markdown template, a length in sentences or items.
- **Typical inputs:** three to five real examples.
- **Hard inputs:** empty, very long, ambiguous, in another language, out of scope, hostile ("ignore your
  instructions").
- **What success means** for each, so a case can pass or fail.

Without this, you cannot tell whether a new version is better.

## 2. Write the first version

A structure that works for most prompts:

1. **The task and its context:** what the model is doing, for whom, and why it matters. Context the model cannot know
   (the domain, the users, the conventions) goes here.
2. **Constraints:** format, length, tone, scope, and what to do when the input is out of scope or unclear.
3. **Examples:** one or two input and output pairs that show the format and a hard case. Make them varied, or the
   model copies their surface.
4. **The input,** clearly delimited (with tags or a fenced block), so text inside it is read as data, not as
   instructions.

Guidelines:

- Say what to do rather than only what not to do, and give the reason; a model generalises from reasons better
  than from bare rules.
- Replace vague words with checkable ones: "at most three sentences" instead of "concise".
- Put information the model lacks in the prompt instead of hoping it knows: definitions, lists of allowed values,
  current facts.
- Ask for reasoning before the answer only when the task needs it, and keep it separate from the part that is
  parsed.
- No personas or flattery ("you are a world-class expert"); they add length, not accuracy.

## 3. Test on fixed cases

Run every case against the model and settings that will be used for real, and read every output. Note each surprise:
those are the bugs.

In YAAP, keep the cases as eval cases in the project (`.blueprint/evals/`): a prompt, a start state and checks, for
example a `file` check on the output's shape and a `rubric` check for what a person would judge. Run them with
`/eval`, and compare runs in the Evals tab. For a Blueprint skill or standard, the eval cases measure whether the
change helps the agent.

Aim for at least one case per typical input and one per hard input. Add a case for every bug found later, before
fixing it.

## 4. Change one thing at a time

1. Pick the most important failure.
2. Make one change aimed at it.
3. Rerun all cases, not only the failing one: a fix for one case often breaks another.
4. Keep the change only if the total got better, and note what changed and what it did.

Model output varies between runs. Run each case more than once before trusting a small difference, and prefer
checks that do not depend on exact wording.

## 5. Keep it maintainable

- Keep the prompt in a file under version control, not as a string buried in code. Commit each version with a
  message that says what changed and why.
- Record the model and settings it was tested with; a model update is a reason to rerun the cases.
- Write down known limits: inputs it still handles badly.
- When text from users, web pages or documents goes into the prompt, assume it may contain instructions. Delimit it,
  tell the model to treat it as data, and test with a hostile case. Delimiting helps; it is not a guarantee, so do
  not give the model permissions that an injected instruction could abuse.

## Report

```
Prompt: <file and version>
Cases: <number, and how many pass before and after>
Changed: <each change and the failure it fixed>
Known limits: <inputs that still fail, or "none found">
Not tested: <models, settings or inputs not covered>
```
