---
description: Writes a specification with the user from one of four templates (feature, bug, refactor, chore), with acceptance criteria YAAP can check where a check is given.
when: When a piece of work is specified before it is planned and built, such as a card's specification.
metadata:
  sources:
    - own
---
# Write a specification

Write a specification for the work below together with the user, using the template that follows. A specification
says what must be true when the work is done, not how to build it: the plan comes later.

1. Read what is known: the request, the conversation and, where it helps, the project's files. Fill in what you can.
2. Ask about what is missing or vague, a few questions at a time. Keep asking until every acceptance criterion is
   concrete: someone who did not take part in the conversation can tell whether it is met.
3. Prefer criteria with a check YAAP can run. End such a criterion with one code span:
   - `` `run: <command>` `` for a command in the project that must succeed, such as a test or a type check;
   - `` `file: <path> exists` `` or `` `file: <path> contains <text>` `` for a file in the project, with a path
     relative to the project, using `/`.
   A check is one line. A criterion without a check is judged by the reviewer, so word it so it can be judged.
4. Keep every heading of the template. Write *None* under a heading that has nothing to say, rather than leaving it
   out. Keep to at most 30 acceptance criteria; split the work when it needs more.
5. Put open points under *Open questions* rather than guessing. Name what the work does not include under
   *Out of scope*.

When the specification is complete and the user agrees, hand it over. When the conversation belongs to a card and you
have a tool that writes the card's specification (`update_card` with `specification`), write it with that tool.
Otherwise give the whole specification as one Markdown block in your reply, so it can be copied as it is.
