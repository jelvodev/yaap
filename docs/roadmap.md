# Roadmap

Where YAAP is going. Each version has a theme. The order is what is intended, not a promise.

- **Numbers.** Every item has a number to refer to, and a number is never reused.
- **Finished items** leave this list. The release notes say what came in each release.

The current version is **0.6.2**.

## 0.8.0: the project board

Plan, build and review the work of a project, steered from one board.

75. **A specification to start from.** Templates for a specification with acceptance criteria, so every piece of
    work starts the same way. The review checks the result against those criteria.
18. **Plan, then build.** `/plan` makes a plan without changing anything: steps, files and checks. After you approve
    it, the worker model carries it out and ticks the steps off.
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

## 0.8.1: public and easier to start

Making the repository public once the project board is in, then fewer steps and less waiting before the first
answer.

51. **Checks on every push** in GitHub Actions: the backend tests, the migration check, the frontend build and whether
    the generated API types are up to date.
48. **Releases on GitHub.** Each release is published as a GitHub Release, so the update notice finds it.
49. **Make the repository public**, and check that the update notice in a running YAAP sees the release.
50. **A clean install** from the public repository on another computer, from `git clone` to a first command in a
    project: its tests run after an approval in the chat, with their output shown. With the time it took.
45. **Ready-made images.** Each release publishes YAAP's images, so installing and updating download them instead of
    building on your computer, and the runner's image arrives when it is first needed. Building locally stays
    possible. Once the repository is public, GitHub Actions builds them for free.

## Later

The direction after 0.8, not yet in a version.

26. **How much the agent steers the board**, set per project:
    - four levels, from only proposing to an autopilot up to a pull request;
    - gates per column;
    - a budget per card and per version.
73. **From a pull request to a release.** After the review and green checks: merge, a release with its notes, and
    optionally a deploy, each step with your approval or by the board's autonomy level.
74. **How the work goes, across projects.** Cards finished per week, how many were right the first time, what a
    card costs, and which kinds of work a cheaper model handles well.
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
81. **Independent tool calls together.** The agent runs tool calls that do not depend on each other in one turn, so
    an answer needs fewer model calls.

## Wish list

Ideas without a version.

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
    run or send the request to a local model. Builds on the breakdown of 66.
65. **Build it like last time.** Mark a screen, route or service that came out well as the example for its kind.
    The next one of that kind starts from it, with the same layout and components, so it is right the first time
    instead of after corrections.
76. **YAAP in a team.** Each developer keeps their own YAAP. The team shares a Blueprint layer through git
    (standards, skills, agents, patterns and eval cases, changed by pull request), one board per project with who
    works on which card, and a team policy for models and budgets that every YAAP applies.
    - Still open: whether a team lead sees costs per person or only totals, and who decides on the team's Blueprint:
      reviewers of its pull requests or one owner.
77. **A team server.** A YAAP on a server for a whole team: sign-in with the company account, roles, a vault per
    person and one for the team, and a runner per person so nobody sees another's work in progress. It can also
    build cards overnight within a budget. Personal YAAPs can connect to it for the team's board and Blueprint.
    - Still open: a hub for the board, the Blueprint and the numbers first, or a YAAP for several people in the
      browser right away; whether personal YAAPs and a server can share one board; how the overnight builds share a
      budget and a subscription's limits; what licences and terms allow for coding CLIs on a shared server.
78. **Skills from your own work.** When the same way of working comes back in a project, YAAP proposes a new skill
    for it. It is measured with the evals before you accept it, and skills that no longer help are proposed for
    removal. Nothing is written without you.
79. **YAAP in your editor.** YAAP speaks the Agent Client Protocol (ACP), so you can work with it from Zed, JetBrains
    and other editors, with your Blueprint, permissions and runner.
    - Still open: other agents that speak ACP as workers, only when they can run in the runner or a sandbox.

For the code base itself:

8. **Linting and formatting.** One pass of ruff, mypy, ESLint and Prettier over the whole code base, with a script
   to run them.
35. **More checks in GitHub Actions:** linting and the audit on every pull request, and Dependabot.
80. **Fairer token measurements.** Each eval play records how many model calls it made, and a savings case runs
    several replies in YAAP's own loop, so the context savings can be measured apart from the number of calls.
82. **Tidy git transports.** A project's transport repository is removed with the project, and changes move in
    smaller bundles instead of whole ones held in memory.
