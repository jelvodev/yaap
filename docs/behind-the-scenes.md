# What YAAP does behind the scenes

Besides answering you, YAAP runs a few tasks of its own. This page lists each one: when it runs, what it costs and
how to change it or switch it off. Settings are named as they appear in Settings; the key in brackets is the one in
[configuration.md](configuration.md).

## Small tasks on the utility model

Some tasks need a model but not a large one. They run on the **utility model**: the preset's utility model, else the
setting *Utility model* (`utility_model`, Settings › Models › Utility and workers; Claude Haiku by default), at low
effort. *Utility model per task* (`utility_task_models`) gives a task a model of its own, for example a local Ollama
model for titles, which then costs nothing. Every call is recorded as utility usage with its tokens and cost (a
subscription connection costs nothing per call), and *Show utility messages* in the Preferences menu shows them in
the chat. A monthly budget that is reached also stops utility calls on API keys, and the model policy applies to them
as to any model.

| Task | When it runs | How to change it or switch it off |
|---|---|---|
| Conversation title | After the first reply of a new conversation | Switch it off in Settings › Models › Background tasks; or route it to a cheaper or local model per task (*Conversation titles*) |
| Memory suggestions | In a project conversation, after a goal ends or every ten of your messages; together with the title when both are due | Switch it off in Background tasks; or route it per task (*Memory suggestions*); nothing changes until you accept a suggestion |
| Memory tidying | After every 20 new memory entries, and with *Tidy up* in the Memory tab | Switch it off in Background tasks; or route it per task (*Memory tidying*) |
| Learning proposals | After a goal in a project ends with signals (declined actions, failing tests); also with *Learn from this* and *Find contradictions* | Switch it off in Background tasks (this also stops *Learn from this* and *Find contradictions*); or route it per task (*Learning proposals*) |
| Code review by another vendor | After a goal or a scheduled task that changed code, and after a card's build (the card's review) | Switch it off in Background tasks; or route it per task (*Code reviews*); without a model of another vendor the review is skipped |
| Current state of projects | After a goal in a project ends, or when a long conversation goes quiet, when the agent has not updated the block itself | Switch it off in Background tasks; or route it per task (*Current state of projects*); your own edits to the block always win |
| Reply routing | Before each reply, only when the conversation's preset has routing on | Switch routing off in the preset (Settings › Models › Presets; off by default), or in Background tasks |
| Check of approvals after outside content | Each approval asked after the agent read a web page, an attachment or a skill from GitHub | *Check approvals after outside content* (`injection_check`), off by default; it cannot be switched off in Background tasks, and is always on the utility model itself, never a per-task one |
| Summary of a long conversation | When a reply ends with the context at 70 % of the limit, with `/compact`, or with *Summarise now* | *Compact automatically* (`auto_compact`, on) and *Summarise at* (`compact_at`, 70) in Settings › Models › Context; runs on the preset's worker model, not the utility model |
| Commit message, pull request text | Only when you ask for one in the Git panel | Started by you, so it has no switch; route it per task |
| Rule from a message, eval case proposal, front matter on import | Only when you use those buttons | Started by you, so it has no switch; route it per task |
| Rubric check of an eval play | When an eval case with a rubric is played | Runs only with evals, on a scoring model YAAP picks; its cost is shown apart |

A task can be switched off in Settings › Models › Background tasks (setting `utility_tasks_off`, per environment or
for all). That page also shows each task's model, its runs and cost this month and its last runs. The check of outside
content and the tasks you start yourself cannot be switched off there.

YAAP's own helpers are shown in the Blueprint as a read-only **Built-in** layer. Their instructions can be replaced by
an item of the same name in Global or an environment (the worker and the explorer also in a project); the guard,
which checks outside content, cannot be replaced.

When no model can be used for a task (no key for its connection, or the model policy allows none), the task is
skipped and the work goes on without it; a task the model policy refused says so. Learning and eval case proposals
then still make the plain cases, such as a project rule from an action you declined with a reason.

## Rules that need no model

These run on every reply or now and then, without a model call.

- **Prompt cache.** With an Anthropic API key, the instructions and the conversation stay in the prompt cache for five
  minutes, or for an hour for a goal, a scheduled task, or a message after a pause of more than five minutes. An hour
  costs more to write but saves rereading the history at full price after a pause. No setting; other connections
  manage their own cache.
- **Shorter context.** When a reply ends with the context at half the limit, old tool output is shortened
  (*Shorten old tool output at*, `trim_at`, 50); at 70 % the earlier messages are summarised (above). *Context limit*
  (`context_limit`) sets the limit; the default is the model's window.
- **Memory recall.** A project memory under about 4,000 tokens goes into the prompt whole. A larger one goes in with
  its recent entries and the entries closest to your message, found by meaning with the local search model. No model
  call and no cost; with *Embedding model* (`embedding_model`) off it matches words only.
- **Repository map.** At the start of a project reply, a map of the project's folders and main files, made once per
  commit (*Repository map*, `repo_map`, on).
- **Blueprint version.** At the start of a project reply, YAAP notes the git commit of each Blueprint layer (one
  `git status` per layer), so a replay can use the same instructions later.
- **Outgoing check.** Every request to a model and every message to another agent is checked against the known
  secrets and the patterns of the model policy before it leaves YAAP. Always on; *Model policy* (`model_policy`,
  Settings › Models › Policy) adds patterns and limits which models may be used.
- **Budgets.** Before each request on an API key YAAP compares the month's spending with *Monthly budget*
  (`budget_total`, `budget_environment`; empty means no limit) and stops a reply that reaches it. Subscriptions are
  never stopped.
- **Idle stops.** Things the agent started stop by themselves when nobody uses them: background processes after an
  hour, shells after two hours, MCP servers over stdio after ten minutes, the headless browser after fifteen minutes,
  and a project's sandbox after an hour. A sandbox or runner with a running process is not stopped. No setting.

## Now and then

- **The minute loop.** Every minute YAAP starts scheduled tasks that are due, ends finished ones and does the
  housekeeping below. A scheduled task's run costs what its replies cost; pause or remove the task to stop it.
- **Search index.** New conversations, memory and Blueprint files are indexed for search by meaning in the
  background, with the local model; no cost. *Embedding model* off stops it.
- **Search model download.** After the first start YAAP downloads the search model (about 2.3 GB) into the data
  folder once. *Download the search model by itself* (`search_model_download`, on) switches this off; Settings ›
  System › Models downloads or removes it by hand.
- **Fetching repositories.** Every 15 minutes YAAP fetches each repository with a remote, so the Git panel shows
  what changed elsewhere. It only updates the remote-tracking branches, never your files. No setting.
- **Board triggers.** A trigger in a project's board settings is a scheduled task that checks CI, labelled issues,
  dependency pull requests or a command and puts a card in Inbox, without a model call. Each makes at most its number
  of cards a day (3 by default); pause or remove the trigger to stop it.
- **Pulling a board repository.** A board kept in a separate repository is pulled in the background at most once a
  minute, fast-forward only. No setting.
- **Review after a build.** When a card's build is done, YAAP runs the acceptance-criteria checks and starts the
  card's review by itself.
- **Cleaning up builds.** Copies of builds under `.yaap/builds` are removed: failed and discarded ones after a week,
  done ones once merged or after two weeks. No setting.
- **Backups.** On the *Backup schedule* (`backup_schedule`, daily at 03:00 by default), in Settings › System ›
  Backup.
- **Update check.** A request to GitHub for the newest release, at start and on the *Update check* schedule
  (`update_check_schedule`, daily at 09:00); `off` stops both.
- **Cleaning up.** Stored tool output after 30 days, what a model call saw (the trace) 30 days after its last use,
  checkpoints after 30 days (at most 500 per project; checkpoints an eval case uses are kept), eval workspaces 24 hours
  after a play that did not pass, the vault's use log after 90 days and the audit log after a year. No setting.
- **OpenTelemetry.** With an *OpenTelemetry endpoint* (Settings › Usage and audit › Telemetry), each reply's trace
  is sent there after the reply. Empty: nothing is sent.
