# Roadmap

Where YAAP is going. Each version has a theme. The order is what is intended, not a promise.

- **Numbers.** Every item has a number to refer to, and a number is never reused.
- **Finished items** leave this list. The release notes say what came in each release.

The current version is **0.6.1**.

## To do before going public

The repository becomes public on Friday 13 November 2026. Before that:

46. **Check the history and files** for anything private: names, addresses, keys, internal notes.
47. **The fixes from the security check**, before everyone can read the code, each with a test that runs on Linux
    the way YAAP runs in Docker.
51. **Checks on every push** in GitHub Actions: the backend tests, the migration check, the frontend build and whether
    the generated API types are up to date.
48. **Release 0.6.1** with the smaller first download, as a GitHub Release, so the update notice finds it.
60. **A front page that shows it.** A short animation and three screenshots in the README, the features that set
    YAAP apart (evals, replay, a review by another vendor, the live preview), and an honest comparison with other
    tools.
49. **Make the repository public**, and check that the update notice in a running YAAP sees the release.
50. **A clean install** from the public repository on another computer, from `git clone` to a first command in a
    project: its tests run after an approval in the chat, with their output shown. With the time it took.

After that, ready-made images (45) can be built for free by GitHub Actions.

## 0.6.x: easier to start

Fewer steps and less waiting before the first answer, and what makes YAAP different within one click.

45. **Ready-made images.** Each release publishes YAAP's images, so installing and updating download them instead of
    building on your computer, and the runner's image arrives when it is first needed. Building locally stays
    possible.
61. **Bring your setup.** Instructions, skills, agents and commands from Claude Code, Codex, Cursor and `AGENTS.md`
    become Blueprint items in one step, with a review before anything is written.
62. **A second opinion.** One click under a reply asks a model of another vendor to check the answer or the changes.
    Its findings show beside the reply, and nothing is applied by itself.

## 0.7.0: the project board

Plan, build and review the work of a project, steered from one board.

52. **One reply at a time, no lost messages.** The server decides when a reply starts, so two sends at once never
    start two replies. A queued message leaves the queue only when its send is accepted.
53. **Replays and evals that say what they prove.** A replay keeps the Blueprint version of the original run and shows
    which context it built again; with all fields empty it uses that version. An eval keeps failed checks apart from
    stopped runs and errors, so those do not pull the pass rate or the cost down.
58. **Less context, same answers.** A file that has not changed since it was read is not sent again, old reads of a
    file that changed since are left out, and test output keeps the failures and the summary. What is left out can
    always be read again. Switched on only when the evals show that quality holds.
18. **Plan, then build.** `/plan` makes a plan without changing anything: steps, files and checks. After you approve
    it, the worker model carries it out and ticks the steps off.
59. **A lead model with cheap helpers.** The main model splits the work into small tasks, each with a check such as a
    test. A cheaper model reads and does the work, YAAP runs the check, and after two failures the main model takes
    over. Several readers can work at once.
19. **Current state per project.** The project memory keeps a short *Current state*: where the work stands, what is
    open, the next step. A new conversation starts from it.
54. **One card from start to finish first.** A request in the chat becomes a card. You approve its plan, one build runs
    in a temporary clone, and you review the diff and the checks that ran. After a restart the card shows where the
    work stands and what is needed next. The rest of the board builds on this route.
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
55. **A review with evidence.** A card's review shows the diff, the acceptance criteria, the tests that ran, the trace
    and, with a preview, a screenshot before and after. Per criterion it shows what YAAP actually checked, with a link
    to the source.
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

35. **More checks in GitHub Actions:** linting and the audit on every pull request, and Dependabot.
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
56. **From a correction to a test.** Mark a wrong answer in the chat and say what it should have done. YAAP proposes an
    eval case and perhaps a Blueprint change, plays the old and the new version from the same start and shows the
    difference before you accept the rule.
57. **See the context before switching models.** On request YAAP shows which files, memory and tool results would go
    to the chosen model, where they come from and which project policy applies. You can leave a source out for that
    run or send the request to a local model.

For the code base itself:

8. **Linting and formatting.** One pass of ruff, mypy, ESLint and Prettier over the whole code base, with a script
   to run them.
9. **A baseline measurement.** The bundled eval set is run and the result is recorded, so later versions can be
   compared with it.
