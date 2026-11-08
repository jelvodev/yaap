# Roadmap

Where YAAP is going. Each version has a theme. The order is what is intended, not a promise.

- **Numbers.** Every item has a number to refer to, and a number is never reused.
- **Finished items** leave this list. The release notes say what came in each release.

The current version is **0.5.4**.

## 0.5.x: fixes and hardening

Patch releases: no new features or settings, so you can update without reading the notes first.

1. **Background work stops cleanly.** Summaries, learning after a reply, project consolidation, the OpenTelemetry
   export, replays, eval runs and the stop button finish or stop their work, instead of leaving a worker thread
   running.

<!-- A gap in the numbers: finished items left the list. -->

3. **Forbidden models marked.** The chat's model lists mark the models that the environment's model policy does not
   allow.
4. **Skipped utility tasks shown.** When the model policy skips a utility task (a title, a commit message), the app
   says so, not only the log.

## 0.6.0: ready for others

For people who install YAAP from this repository.

11. **Releases and a changelog.** Tagged releases with notes. Each release says what you must do yourself when you
    update.
12. **Updating with one command.** `scripts/update.ps1` and `scripts/update.sh` do four things:
    - make a backup;
    - build and start the newest release;
    - check that it answers;
    - go back to the previous version when it does not.
13. **Update notice.** YAAP looks for a newer release when it starts and on a schedule (daily by default; you can
    switch it off). Settings then shows the release notes and the command to update.
14. **First run in the app.** A guided start in the browser: a model connection, the first environment and project,
    and where backups go.
15. **Any OpenAI-compatible endpoint.** A connection with a base URL and an API key, for OpenRouter, LM Studio, vLLM
    and similar servers.
16. **Hidden characters.** Skills, Blueprint packages and imports are checked for invisible, direction-reversing and
    look-alike characters. Any such characters are shown before you accept the item.
17. **Move a conversation to another project**, together with what belongs to it.

## 0.7.0: the project board

Plan, build and review the work of a project, steered from one board.

18. **Plan, then build.** `/plan` makes a plan without changing anything: steps, files and checks. After you approve
    it, the worker model carries it out and ticks the steps off.
19. **Current state per project.** The project memory keeps a short *Current state*: where the work stands, what is
    open, the next step. A new conversation starts from it.
20. **The project board.** Clicking a project opens its board.
    - The columns are Inbox, Idea, Design, Plan, Build, Review and Done.
    - Cards belong to a version, which shows its progress and budget.
    - Each column has one action that carries out its step: specify, plan, build, review.
21. **Cards in git.** Cards are Markdown files kept on a branch of the project's repository, or in a separate private
    repository. They never mix with the work on other branches.
22. **Cards from the agent.** The agent adds what it notices to the Inbox, checks first for a similar card, and names
    where each card came from. GitHub issues can be imported as cards.
23. **Building in parallel.** Each card is built in its own worktree or sandbox. Cards that touch the same files never
    run at the same time. A review by an agent that did not write the code comes before you merge.
24. **Waiting for me.** One overview across projects of what needs you: plans to approve, reviews, questions and new
    cards.
25. **Triggers.** A failing CI run, a new issue or a dependency update becomes a card in the Inbox, within a budget.

## Later

The direction after 0.7, not yet in a version.

26. **How much the agent steers the board**, set per project:
    - four levels, from only proposing to an autopilot up to a pull request;
    - gates per column;
    - a budget per card and per version.
27. **Cheaper building, same quality.** A cheaper model builds and a stronger one plans and reviews. After failed
    attempts the stronger model takes over. Cost and attempts are measured per card.
28. **Project health.** Test coverage, dependency age, advisories and notes such as TODO and FIXME. A weekly chore
    adds the most useful fixes to the Inbox.
29. **Updating from the app**, with one click.
30. **1Password as a source for the vault.**
31. **More for plugins:** hooks, API routes, extra packages, reloading without a restart, and server events for
    plugin pages.
32. **Targets over SSH:** the git tools and checkpoints on a target, and a restore point on Windows before full
    access.
33. **The runner:** a panel with its state, *Stop* and *Rebuild*. A sandbox project's MCP servers and browser run in
    its sandbox.
34. **A model policy at the global level.**

## Wish list

Ideas without a version.

35. **Checks in GitHub Actions:** tests, linting and the audit on every pull request, and Dependabot.
36. **Less code.** A skill that makes the agent prefer the standard library and the platform's own features, kept
    only where the evals show that quality holds.
37. **A code graph.** A graph of the code over MCP, compared with the language servers using the evals.
38. **Your own browser.** The agent drives a browser on your computer, with your sign-ins, and asks for every action.
39. **Office documents and a canvas.** Documents, spreadsheets and presentations that change in a panel beside the
    chat.
40. **Computer use.** The agent works in apps without an API, through screenshots, mouse and keyboard, on a target of
    its own.
41. **Azure and work modes.**
42. **YAAP on the phone.** An installable app with approvals, the board's *Waiting for me* and push notifications.

For the code base itself:

8. **Linting and formatting.** One pass of ruff, mypy, ESLint and Prettier over the whole code base, with a script
   to run them.
9. **A baseline measurement.** The bundled eval set is run and the result is recorded, so later versions can be
   compared with it.
