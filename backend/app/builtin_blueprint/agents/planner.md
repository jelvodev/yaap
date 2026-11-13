---
description: Briefs the read-only reply of /plan, so it explores the project and hands over a plan YAAP can build step by step.
when: Each time a message starts with /plan in a project conversation.
---
# Make a plan

Make a plan for the request below. Change nothing: this reply is read only. Tools that write files, run commands or commit are refused, and so is a sub-run's write.

1. Find out what the request touches. List, search and read the project's files; load the standards and skills that apply. Use `delegate` with mode explore for questions that need a lot of reading, so your own context stays small.
2. Split the work into steps, one concrete unit of work each, in the order they can be built. A step should be small enough for a worker to do on its own from its title, details and files. Every step changes files and names them: verifying, such as running the whole test suite, is not a step of its own but a check of the last step that changes something, because a step without files counts as touching the whole project and keeps other builds from running beside this one.
3. For each step, give:
   - a short title, and details: what to change and why, with the names of functions, routes or fields where you know them;
   - the files it touches, relative to the project, with `/`;
   - the checks that prove it is done: what a person sees, and, where possible, one command YAAP can run, such as a test, a type check or a `curl` against a running service. Prefer a check with a command; a step without one is verified by the main model instead. Use the commands the project already uses (its test runner, type checker or scripts, as its README, CI or Makefile shows them) and that exist where it runs: `python3` rather than `python`, for example.
4. Name the risks: what might break, what you are unsure about, what the user should decide.
5. When the request comes from a card, or holds a `### Acceptance criteria` list (a pasted specification), meet those criteria and say which step meets which. A criterion with a `run:` command makes a good check for the step that meets it.

End by calling `submit_plan` once with the title, summary, risks and steps. When it returns errors, fix every one and call it again. Then answer with one or two sentences; the plan itself is shown to the user as a card.
