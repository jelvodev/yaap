# Changelog

What changed in each release, newest first. Every release is tagged (`v0.5.7`); update by checking out or pulling
the release, then rebuilding (see [Updating](docs/install.md#updating)). **Action needed** lists what you must do
yourself when you update; it says "None." when there is nothing.

## Unreleased

- `/plan <what>` in a project conversation makes a plan without changing anything: the reply is read only, and the agent hands over its plan as a card with numbered steps, the files each touches, the checks that prove it and the risks. The card can be edited in place or discarded; a new `/plan` replaces the earlier draft. Exports show the plan as a checklist.
- Work that a read-only reply delegates is read only too: a sub-run never gets more access than the reply that started it.
- Each project's memory now has a short **Current state** block: where the work stands, what is open and the next step. Every conversation of the project reads it on every turn, so new work picks up where the last stopped.
- The agent keeps it up to date with the new `update_current_state` tool, at the end of a goal or a build and when you stop long work. When it does not, a background task (*Current state of projects*, switchable under Background tasks) rewrites it after a goal ends or a long conversation goes quiet; your own edits always win.
- Edit the block in the project's Memory tab (with a character counter, Save and Revert). A new conversation in a project shows it, with *Continue from here* and *Edit*.
- New API: `GET` and `PUT /api/v1/projects/{id}/current-state`.

### Action needed

None.

## 0.7.0 (2026-11-12)

- Fetch, push, pull and clone use the GitHub token only in a transport repository that YAAP alone writes, in the data folder; a process in the runner can no longer change how a token-carrying git command connects. Commits move between that repository and the project as git bundles, whose branch names and commits YAAP checks.
- The git panel keeps its connection to the runner for a few minutes instead of making a new one (and a new vault use entry) on every request.
- A queued `/eval` starts at most one eval run, also when it is sent twice or YAAP restarts in between; the run records the queue item it came from.
- A queued `/compact` keeps the conversation reserved while it summarises, so a reply cannot start in the middle of it.
- *Where it goes* in the model setup's Context now breaks the context down as a bar and a table by category (system prompt, Blueprint, tools, messages, the room until YAAP summarises, free space). The total is exact from the last reply; the split is estimated and scaled to it. A category opens to its largest items, with *Show in trace* for tool results. With Claude Code and Codex the CLI's own prompt and history show as one remainder.
- The trace has two more tabs: *Context* (the same breakdown) and *Tokens*: per reply the cache read, cache write, uncached input and output with the cost, workers and background calls as rows of their own, the ten largest tool results, the cache hit rate, and hints where tokens were lost (a file read more than once, a cold cache after a pause, tool results over half of the context, one outside MCP server with a large share of the tool definitions).
- `GET /conversations/{id}/token-usage`: where a conversation's tokens went over the session.
- `GET /conversations/{id}/context` returns categories with their largest items instead of a flat list of parts.
- The token footprint script and the screen count with the same division.
- A setting `context_savings` (off by default) to send less of what the model already has: a repeated read of unchanged lines in the same reply becomes one line; long output of a known test runner (pytest, vitest, jest, go test, cargo test, dotnet test) keeps the failures and the summary, with the whole output readable through `read_output`; when old tool output is shortened, reads of files that changed since are left out with a note to read them again; and a conversation keeps its repository map until it is shortened or summarised. What is left out always says how to get it back.
- The token footprint measures a longer project reply with these savings off and on.
- Small labels under each reply show what happened behind it: the model the route chose, the project memory that went along, helpers, approvals, the injection check, secret values replaced in tool output, output the outgoing check blocked, older output shortened or summarised, and the prompt cache with its hit rate. Each label opens to its reason and cost. Replies made before this version show no labels.
- A "why" popover shows the rule and the setting behind a decision: on the approval card, on a tool call that was refused or asked, on the labels under a reply and on the token hints.
- The trace keeps step notes (memory recalled, cache lifetime, trim and summary, masked secrets by name only, blocked output, the injection check's verdict); they do not count as steps in eval budgets or the OpenTelemetry export.
- New endpoint `GET /api/v1/conversations/{id}/messages/{message_id}/behind`; the `approval_request` stream event and token hints carry a `why`.
- YAAP's own helpers (explorer, worker, reviewer, router, guard, summariser, memory keeper, learner) show in the Blueprint as a read-only Built-in layer, with their instructions, model and cost this month.
- An item of the same name in Global or an environment replaces a helper's instructions (the worker and the explorer also in a project); *Change in my layer* copies one to Global. The guard that checks outside content cannot be replaced.
- Settings › Models › Background tasks lists every task YAAP runs by itself with its model, runs and cost this month and its last runs, and the work it starts on a schedule.
- Background tasks can be switched off one by one (setting `utility_tasks_off`): titles, memory suggestions, memory tidying, reply routing, learning and code reviews. The check of outside content and the tasks you start yourself cannot be switched off.
- The context savings switch is on the Context tab in Settings.
- Coming back to a long conversation after its prompt cache has expired shows, before the next send, what continuing costs (estimated; tokens on a subscription), with *Summarise first* (YAAP's own loop), *New conversation from a summary* (every connection) and *Just send*. Nothing happens by itself.
- A conversation started from a summary links to the earlier one in its heading, and the earlier one links to it.
- An eval case can approve calls up front with `approve` (`tools` and plain `commands`): in that case's own play they pass the injection guard after the run has read outside content, such as an MCP server's output. Only file edits, renames, local commits and listed commands can be approved; ordinary conversations keep the guard as before, and the play's trace shows each call the case approved.
- The baseline case tracker-csv-export approves its edits and the unit test command, so it can pass after reading its issue from the tracker.
- Presets can delegate by brief (Settings › Models › Presets, *Delegation*; off by default): the agent hands checkable work to the cheaper worker with the files, known facts, when it is done and a check command.
- With delegation by brief YAAP runs the worker's check itself, with the conversation's permissions, and gives the agent the worker's report, the check's result and the diff of the changed files.
- A failed check goes back to the worker once; a second failure hands the task to the main model. The trace notes each step back or up, and each attempt shows with its cost.
- With delegation by brief, up to three exploring workers run at the same time.
- Every answer can be seen as a route: message, routing, context, each model call, the tool calls with their checks, helpers and the work done afterwards. While a reply runs, a strip above it grows stop by stop and pulses where it waits for you, such as an approval.
- When a reply is done, a *Route* chip among the labels under it shows its time and cost and opens the trace on the new *Route* tab, where each stop opens a drawer with what happened, why, its time and its cost. Repeated calls of one tool are stacked into one stop.
- The trace dialog's tabs are now Timeline, Route, Context and Tokens.
- The context breakdown shows again how many tokens shortening old tool output saved.
- The trace shows when an eval case's approval let a call through ("approved by the eval case") instead of an empty approval field.
- The summary that starts a continued conversation no longer shows try again, second opinion or versions.
- A click on the context ring opens the model setup with the context breakdown already shown.
- Without a reply language set, replies and conversation titles follow the language of your message; before, a reply or title could come back in another language.
- A browser screenshot the agent saves under a file name now also comes back as an image, so the agent sees it and the chat shows it.

### Action needed

None.

## 0.6.2 (2026-11-12)

- Import can read the setup of Claude Code, Codex, Cursor and GitHub Copilot (a zipped home folder or a project): instructions, rules, commands, agents and skills are mapped, settings, hooks and credentials are never opened, scripts are only written when ticked, and values that look like keys are refused.
- A reply has a second opinion: another model checks the reply's changes, or its answer, and shows the result under the reply after you have seen who reviews and what it can cost.
- API tokens can no longer change or revoke other API tokens through the vault; use a signed-in browser.
- Web fetch checks public destinations on every redirect and connects to the checked address.
- Adding a web page as knowledge and installing a skill from GitHub use the same check: they refuse addresses on this computer or the local network, also after a redirect, and a GitHub token is only sent to the address it was meant for.
- Retrying or regenerating a built-in `/check` keeps the run read only, including for existing messages.
- Resetting a settings list now reports a failed request instead of showing a success message.
- A failed reply start now closes the model connection and revokes the run's tool-server token.
- Git on project folders (status, diff, commits, branches, merges, reviews and the like) now runs in the environment's runner instead of in YAAP; fetch and push stay in YAAP because they use your GitHub token.
- A queued `/compact` or `/eval` is now accepted from the queue and run in turn, instead of waiting as a message.
- A replay without changes now rebuilds its instructions from each Blueprint layer at the version the original run used, and its header says which version each layer came from.
- Eval comparisons count the pass rate and the median cost over plays that passed or failed only, and show errors and stopped plays apart.
- With Anthropic, a conversation that keeps its prompt cache for an hour (goals, scheduled tasks, a reply after a pause) now keeps the conversation history cached for an hour too, not only the instructions.
- New documentation: `docs/behind-the-scenes.md` lists every task YAAP runs by itself, when it runs, what it costs and how to switch it off; `docs/comparison.md` compares YAAP with other tools, and the README names the features that set YAAP apart.
- The changes panel reads the git state again when it opens, so changes made outside YAAP show too; an import on the first-start page now says what it wrote.

### Action needed

None.

## 0.6.1 (2026-11-10)

- The search model (2.3 GB) is no longer in the image: YAAP downloads it into the data folder in the background
  after the first start, and an updated installation fetches it at its next start. The image and the build are that
  much smaller. Until the model is there, search finds words only and says *Search by meaning is not ready yet*;
  conversations and documents are indexed by meaning when it arrives. Settings › System › Models shows the progress
  and has *Download* and *Remove*, and a switch to turn the automatic download off (also on the last screen of the
  first start). The speech model and the search model share one downloader that checks every file by its SHA-256.
- Claude Code and Codex are no longer in the image either: YAAP installs both into `data/tools` with `npm ci` the first
  time you press a sign-in button, or at start when you used them before (about 700 MB, a few minutes; the card shows *Installing Claude Code…*), then starts
  the sign-in. The versions and checksums are pinned per release in `tools/package-lock.json`, so a release that pins
  newer versions installs them again at its next start, and Claude Code is downloaded by your own YAAP, never shipped
  in an image of ours. Before the install, a reply or a worker that needs one of them says it is not installed yet
  and points to Settings › Connections › Providers. The monthly refresh is gone: the update script no longer sets
  `CLI_REFRESH`. `data/tools` is not in backups.

### Action needed

If you use Claude Code or Codex: nothing. At its first start after the update, YAAP sees your sign-in and installs
both programs again from npm (about 700 MB, a few minutes); the sign-in is kept. Until that has finished they show
*not installed yet*. On a computer without access to `registry.npmjs.org` they cannot be installed.

Otherwise nothing. After updating, search finds words only until the model has downloaded (2.3 GB, from Hugging
Face); on a computer without internet access, place the files by hand
([install.md](docs/install.md#the-search-model)).

## 0.6.0 (2026-11-10)

- Claude Code and Codex sign in from the browser: Settings › Connections › Providers and the first start have a
  button *Sign in with your Claude subscription* (open the link, sign in, paste the code back) and *Sign in with your
  ChatGPT plan* (open the link, enter the code shown). The terminal command still works. YAAP only shows links on the
  vendors' own sites, and the pasted code goes straight to the CLI.
- Any OpenAI-compatible endpoint (a keyless server such as LM Studio or vLLM) connects as a provider of its own: add
  its base URL in Settings › Connections, and its models show in the model picker as `<name>/<model>`. Agents run on
  them with YAAP's tools. Keys are not part of this yet.
- YAAP tells you when a newer version exists: Settings › System › Updates shows the newest release with its notes and
  the update commands, a dot shows on Settings, and a page that stays open across an update offers a reload. YAAP
  asks GitHub a few seconds after it starts and once a day; the schedule can be changed or switched off.
- One command updates YAAP: `scripts/update.ps1` on Windows, `scripts/update.sh` on macOS and Linux. It shows
  what changed with *Action needed* first, makes a backup, builds and starts the new version, checks that it
  answers and offers to go back when it does not; `-Rollback` undoes an update that turned out bad. Claude
  Code is now installed afresh once a month, in the first update of the month.
- A conversation can move to another project of its environment, or out of its project. Its agent runs, search
  entries, reviews and suggestions go with it, its processes and shell sessions are stopped first, and a note in
  the conversation tells the next reply that the folder changed.
- Text from outside is checked for hidden characters: skills from GitHub, Blueprint packages, imported documents and
  sessions. Tag characters and text direction controls are refused; invisible characters, stray variation
  selectors and words that mix Latin letters with Cyrillic or Greek ones are shown as warnings in the review, with
  the characters made visible.

### Action needed

The first update to 0.6.0 is by hand: `git pull` and `docker compose up -d --build`; after
that, `scripts/update`.

Skills and packages that contain tag characters or text direction controls are now refused. If one you use is
refused, open the file and remove those characters; the review shows where they are.

## 0.5.7 (2026-11-09)

- A new installation opens a short guide after the password: a model connection, the environment and a first
  project, where backups go, and YAAP's starter Blueprint. Every step can be skipped or left for later; Settings ›
  System › First start runs it again.
- YAAP's starter is a ready set of standards, skills, commands and agents in six packs (Core, Web, Python, Data,
  DevOps, Writing), installed through the usual package review and pinned to this version. It is also under
  Blueprint › Add from a package › YAAP starter. Where its ideas come from is on the credits page,
  [docs/credits.md](docs/credits.md).
- An agent that lists `open_browser` among its tools now gets the browser.
- A link to a private repository without a GitHub token says that the token is needed.

### Action needed

None. An existing installation does not see the first start; install the starter from the Blueprint when you want
it. While the repository is private, reading the starter needs a GitHub token in Settings › Git and GitHub.

## 0.5.6 (2026-11-09)

- Background work stops cleanly: summaries, learning after a reply, memory tidying, the telemetry export, replays,
  eval runs and the stop button wait for their worker threads instead of leaving them running.
- Shutdown stops eval runs and replays before their replies. This finishes 0.5.

### Action needed

None.

## 0.5.5 (2026-11-08)

- Every model list marks the models the model policy does not allow, with the reason.
- When the policy skips a utility task (a title, a commit message, a summary and others), YAAP says so where it
  happened, and the audit log can show only those skips.

### Action needed

None.

## 0.5.4 (2026-11-08)

- A reply that fails on a provider error now records the usage of the model calls that returned before it.
- Python packages are installed from a lock file with hashes, and the base images are pinned by digest.
- `scripts/audit` checks the frontend, the Python packages and the image's npm tools against known
  vulnerabilities.

### Action needed

None.

## 0.5.3 (2026-11-08)

- Documentation in line with the code: wrong names, paths and a broken link fixed, missing settings and parts
  described.
- The update notes say correctly when Claude Code and Codex are updated.
- Linting and the baseline measurement moved to the wish list on the roadmap.

### Action needed

None.

## 0.5.2 (2026-11-08)

- A public roadmap ([docs/roadmap.md](docs/roadmap.md)): what comes in 0.5.x, 0.6.0 and 0.7.0, later, and a wish
  list.

### Action needed

None.

## 0.5.1 (2026-11-08)

- YAAP's own confirm dialog instead of the browser's.
- The page no longer grows taller than the window on short screens.

### Action needed

None.

## 0.5.0 (2026-11-08)

- The calmer chat interface, and the work of versions 1 to 5.
- Fixes: discarding uncommitted changes, the usage of a stopped reply, and background work stopped cleanly at
  shutdown.

### Action needed

None.
