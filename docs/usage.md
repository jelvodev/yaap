# Using YAAP

A tour of what YAAP does and where to find it. The interface has three tabs, **Chat**, **Blueprint** and **Settings**, plus the environment switcher and **Ctrl K** (command palette and search) in the header.

## Chat

The left side lists conversations per project; *No project* holds plain chats. Start a conversation with **New**, choose the agent and model with the button at the left of the input, and type.

- **Model setup.** The button at the left of the input shows the agent, the model and the effort (`YAAP · Opus 5.5 · medium`); the ring around its icon fills as the conversation uses its context, and turns yellow from 80% and red from 95%. Click it for the Agent, Preset, Model, Effort, Context, Limit and *Compact now*. Before the first message they set what the new conversation starts with (a model or effort of your own makes the preset *Custom*); after it, the agent and the limit can still change and apply from the next message on, while the model and effort stay as the conversation started. *Where it goes* under Context shows what takes the space and the cost of the last reply.

- **Attachments.** Paste a screenshot, drop files, or use the paperclip's menu: *Files…*, *A folder…*, *From the project…* (the project's files with their search; a click attaches a copy) and, while the project's preview runs, *Screenshot of the preview*: images and PDF go to the model natively; Word, Excel and PowerPoint as their text; a zip as an overview the agent can read file by file. Scanned pages are read with OCR. A **folder** (dropped, or picked with *A folder…*) is sent as one attachment with its paths and read the same way; `.git` and dependency folders such as `node_modules` are left out, and the limits are those of a zip (5,000 files, 500 MB, 100 MB packed).
- **Branching.** *Edit* on your message sends a new version, the branch icon under a reply (*Try again*) asks again; ‹ 1/2 › switches between versions.
- **Under a reply.** *Copy* copies the reply's text (without its thinking and tool calls) and the branch icon tries again; the ⋯ beside them holds *Show trace* and *Read aloud*. Under the conversation's last reply, in a project, also *Save as eval case* and *Learn from this*: they act on the whole conversation, so they sit only there. The last reply's row always shows; the others show when you hover the reply.
- **The heading.** The title, then one chip for where the conversation works: the project with its branch (*TESTPROJECT_Yaap · main*), and with a computer over SSH *· on devbox* (*· full control on devbox*, in red, with full control); click it for Project settings. While the project has something to do, a second chip on the right says what and opens Changes: *5 changed · Review* for uncommitted changes, *1 to push · Sync* for commits that are not pushed, *5 changed · 1 to push* for both. The ⋯ beside it holds *Export as Markdown*, *Export as JSON*, *Save as eval case*, *Learn from this*, *Show trace* (of the last reply), the latest review of the conversation's changes when there is one, *Rename* (in place, in the heading), *Archive* (*Restore from archive* when archived) and *Delete* (after a confirmation).
- **Commands.** Type `/` for prompt templates from the Blueprint, such as `/review`. In a project, `/goal <objective>` starts goal mode (below). In a project, `/check` checks the project against its standards in a read-only reply, with a verdict per standard and each deviation with file and line; `/check <standard>` checks one. `/compact` summarises the earlier messages now; `/compact <focus>` tells the summary what to keep in detail, for example `/compact keep the API decisions`. A Blueprint command with the same name takes precedence over `/check` and `/compact`.
- **Skills.** Pin a skill from the input to keep it active for the whole conversation. Default skills (Settings › Agent) are pinned in every new conversation.
- **Queue and steering.** While a reply runs, a new message waits in a queue above the input, or *Steer* sends it to the running reply at its next step. Replies run on the server: you can close the tab and come back.
- **Context meter.** The ring of the model setup button, and Context in its popover, show how much of the context limit the conversation uses (with the model's window beside it when the limit is lower), what takes the space, and the cost of the last reply. YAAP shortens old tool output at 50% of the limit and summarises the earlier messages at 70% (the mark on the meter; both can be changed in Settings › Models › Context, or switched off); *Summarise now* does it earlier. Claude Code and Codex compact their own sessions.
- **Context limit.** *Limit* in the model setup: *settings* follows the `context_limit` setting; choose a size to keep this conversation smaller and cheaper. It can be changed later in the conversation and applies from the next message on. The model keeps its full window, so a reply never fails on the limit.
- **Conversation menu (⋮).** In the sidebar: rename, archive (or restore), delete. The heading's ⋯ has the same and the export as Markdown or JSON.
- **Import a session.** *Import a session* at the bottom of the conversation list turns a Claude Code or Codex session into a conversation in the project you choose. Upload the file (Claude Code: `~/.claude/projects/<folder>/<session>.jsonl`; Codex: `~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl`), or pick it on a computer you added under Settings › Targets: *List sessions* shows the newest sessions in the home folder there. The conversation shows the original messages, thinking summaries and tool calls with their results as they were, marked *Imported*; nothing in it is run again. A Claude Code session rewound to an earlier point is imported as it ended. Continue it like any conversation, with the preset chosen next to the input; the model gets the earlier tool calls as text. Files up to 50 MB.
- **In replies.** Code is coloured; an `svg` block shows as an image and a complete `html` page as a live, sandboxed preview.
- **Remember as rule.** On any of your messages: turn a correction into a rule in your Blueprint, previewed as a diff.
- **Learn from this.** Under the last reply of a project conversation, or in the heading's ⋯: YAAP looks at what went wrong in the conversation (actions you declined and why, a test that failed twice, searches for what the Blueprint already says, your corrections) and proposes rules, memory lines or skill steps. They wait in the project panel's **Learning** tab; nothing changes until you accept one. YAAP also does this by itself when a goal or a scheduled task ends with such signals.
- **Speaking.** The microphone next to the paperclip records a message; click it again to stop (at most five minutes). YAAP transcribes the recording itself, with a local Whisper model, and puts the text in the input field, after what you typed, for you to check and send. The audio never leaves your computer. The first click downloads the speech model once (about 486 MB; the note above the microphone shows the progress); click again when it says the model is ready. The reply language (Settings › Agent › Language, or the project's) sets the language Whisper listens for; without one it recognises the language itself. The browser asks once for permission to use the microphone, and only allows it when YAAP is opened at `localhost`, `127.0.0.1` or over https.
- **Trace.** *Show trace* in a reply's ⋯ (or in the heading's ⋯ for the last reply): what the reply did, step by step. Every model call and tool call shows as a row with a bar for when it ran, its duration and its cost; work the agent delegated shows as an indented run right after the `delegate` call that started it, with its own steps (a delegated run that delegated again goes one level deeper). Choose a step for its details: a tool call's arguments, result, stored output and approval; a model call's model, tokens and cost, and *What it saw*: the context of that call grouped by where it came from (the base prompt, rules, standards, skills, the agent, the project with its memory, the tools, and the messages), with the size of each part. Open a part to read it. A tool call's details in the reply (click its line) have *Show in trace*, which opens the trace on that call's step. What a call saw is kept 30 days after it was last used; after that the trace says it is gone. Secrets appear only as `[secret:NAME]`.
- **Replay from here.** On a model call of a reply in a project (in its *Trace*), *Replay from here* starts a new run that begins with exactly what that call saw, in a throwaway copy of the project's files as they were at that moment, with another preset, another model or a Blueprint layer at a git branch if you fill them in (all empty replays it as it was). The replay runs on its own, like an eval case: nobody approves, so a call that would ask is refused and counted as *refused as asked*. It opens beside the original: per side the steps, tokens, cost and final answer (the original from the replayed call on), and both timelines; *Stop* stops it. The replays of a call are listed under it. A replay is a separate, archived conversation; the original reply, its conversation and the project's folder are never changed. Earlier tool calls reach the replayed model as text notes with their results, and secrets only as `[secret:NAME]`. Only a reply's own model calls can be replayed (not those of delegated work), and only while what they saw is kept (30 days). **Make eval case** turns a finished replay into an eval case in one step: it starts at the checkpoint with those files, which is then kept for good (no longer removed after 30 days), plays the replay's prompt, and is checked by the project's test command and a rubric with the replay's answer as reference; it is written to `.blueprint/evals/` as one commit, where you can edit it.
- **Read aloud.** In a reply's ⋯: the browser reads the reply with its own voice, leaving out thinking, tool calls, code blocks and links' addresses; *Stop reading* ends it. *Read every reply aloud* in the Preferences menu reads each reply when it finishes; this choice is kept per browser. The voice follows the reply language, or the language the reply is written in. YAAP prefers a voice that runs on your computer; install one for your language in the operating system if the browser only offers online voices (those send the text to the browser's maker).

### What the agent can do

In a project, the agent works in the project folder with YAAP's tools: read, search, write and edit files, run commands and background processes (such as a dev server), git status, diff and commit, web search and fetch, a headless browser with screenshots (the agent opens it with `open_browser` when a reply needs it, so replies that do not use it carry none of its tools), the project's memory and knowledge, and delegation to a worker model. Outside a project it can still search the web, read pages and use skills. Tools of outside MCP servers come along in both: a server with more than five tools is listed by name, and the agent adds the ones it needs with `find_tools` (the setting `list_all_tools` per server changes that; see [configuration.md](configuration.md#mcp-servers)).

**Large files and long output.** The agent reads a file 400 lines at a time unless it asks for a range, and can first ask for a file's outline: its classes, functions and methods (Python, TypeScript and JavaScript) or headings (Markdown) with their line numbers, so it reads only the part it needs. A `read_file` call in the chat shows which lines were left out. Tool output over 6,000 characters, such as a long test run, is kept whole: the agent sees its start and end and reads the rest when it needs it, and the chat links to the full output.

**Code intelligence.** In a project with Python, TypeScript or JavaScript files (and Go, Rust or C/C++ when their language server is installed in the runner, see docs/configuration.md), the agent can ask the project's language server where a symbol is defined (`find_definition`), where it is used (`find_references`), what errors and warnings a file has (`diagnostics`), what classes and functions a file has (`document_symbols`), and rename a symbol in every file that uses it (`rename_symbol`). The server runs where the project's commands run (the environment's runner or the project's sandbox), starts the first time it is needed and stops after an hour without use. Once it runs, every `edit_file` and `write_file` result in the chat ends with the file's new errors, so a type error shows right after the edit, and the outline of a file comes from the server. A rename changes files like an edit: it takes one checkpoint before it, so *Roll back* undoes it in all files at once, and it asks for approval exactly when *Edit files in the project* is set to ask in Settings. The agent names a position by file, line and the symbol's name on that line; YAAP finds the column itself.

**The repository map.** A conversation in a project starts with a short map of the project: its folders, its main modules (source folders and entry points first, tests last) and their public classes and functions, about 1,500 tokens, so the agent knows where to look without searching first. It follows `.gitignore` and leaves out folders such as `node_modules`, `.venv`, `dist` and `build`. YAAP makes it once per commit (and once more when there are uncommitted changes) and then reuses it, so it does not slow replies down or change while you work; the agent's tools always show the current files. Switch it off for a project, an environment or everywhere with *Repository map* in the Context settings, for example to compare eval runs with and without it.

**Where commands run.** The agent's commands, background processes, shell sessions, MCP servers over stdio and its browser run in the *runner* of the environment: a Debian container that holds only this environment's project folders, as `/workspace` (a project is `/workspace/<project>` there), and cannot reach YAAP itself. A command starts in the project folder; the agent may install packages with `sudo`, and they stay until the runner is made again after an update. Its files are the same files YAAP shows: file tools, checkpoints, git, the Files tab and the editor work on them in YAAP. The browser's `localhost` is the runner, so a dev server the agent started there opens without asking. The runner starts by itself on the first command and stops after an hour without use; it can reach the internet unless *Runner network* in Settings › Targets says *Offline*. A project that chose its own sandbox or a computer runs its commands there instead (below). If the runner cannot start, a command fails with what to do (see [install.md](install.md#the-runner)); YAAP never runs it in its own container. There is no Docker in the runner or a sandbox (they are unprivileged containers): the agent runs a web app as a background process with its port, which shows it in the preview beside the chat, and it can still write a Dockerfile or compose file for you to run on your own computer. The agent also knows where the project folder is on your computer (for example `C:\YAAP\data\workspace\private\site`), so it names that path rather than guessing one.

**Git that YAAP runs itself.** The git tools, the project panel, checkpoints and the periodic fetch run in YAAP, on the same folder the runner writes. So that a command cannot plant something there that YAAP would run, YAAP's git switches off hooks, pagers, editors and credential helpers from a repository, refuses filters and diff or merge drivers (below), and ignores your own `~/.gitconfig`. When a project's `.git/config` sets anything beyond plain settings (remotes, branches, user, core options), for example a `filter.*` from Git LFS or a `core.fsmonitor`, YAAP refuses git in that project and names the key to remove; your own git outside YAAP keeps working with it. The git commands the agent runs in the runner are ordinary git and are not limited this way.

**Shell sessions.** When the agent runs commands in a named session, the folder it moved to and the variables it exported (an activated virtual environment, for example) are still there in later replies of the same conversation. A session ends after two hours without use, when you delete the conversation, or with *End session* under Processes in the project's settings, which lists the open sessions. Shell functions and unexported variables do not carry over, and secrets are never stored with a session.

**On this computer.** With a target from Settings › Targets, the agent works in a folder of your own computer instead of the runner: a project chooses it in its settings, a conversation outside a project with the *on ·* choice before its first message. The project chip in the heading then ends in *on <computer>*. There every change asks first and cannot be rolled back; git tools and checkpoints work only on YAAP's own project folders (runner and sandbox). Shell sessions work there too, with bash and PowerShell; their state is a small `yaap-shell-*` file in the computer's temp folder. See [install.md](install.md#let-yaap-work-on-this-computer).

**Delegation and workers.** The agent can hand a clear task to a worker, which works in the same project and reports what it did and which files it changed. By default that is the worker model of the conversation's preset. Ask for another one by name: a preset ("let Deep do it"), *Claude Code* or *Codex*; Settings › Models › Workers lists them with their models. The two coding agents run as their own CLI on your subscription (signed in as under Settings › Connections), with only YAAP's tools: the same project or computer, the same permissions and access level as the conversation, and their approvals in this chat marked `[Claude Code]` or `[Codex]`. Their usage counts as subscription use. The worker's steps and report show on the `delegate` call, in the reply's *Trace* and in the agent's Runs tab. To find something out (where a feature lives, how a part works) the agent can send a worker *exploring*: it only reads and searches, never asks for approval and cannot change anything, and its report of files, line numbers and a conclusion is kept short, so the conversation stays small. Its label in the chat ends in *exploring*.

**Routing.** A preset can choose per reply which of its models answers. In Settings › Models › Presets, tick *Route each reply by the kind of message* under the preset and choose for questions, mechanical changes, planning and review whether its main, worker or utility model answers (all start on the main model). Before each reply the utility model names the kind of your message; the model chosen for that kind then answers the whole reply. A routed reply shows the model that answered and why below it. When the utility model gives no clear answer, when the preset has no model for that role, or when a model is not allowed here (the model policy, the budget, a missing key, or a model of another provider than the conversation), the main model answers and the note says why. Goals and scheduled tasks route each turn. Routing costs one small utility call per reply (*Reply routing* in the utility tasks, where it can get a model of its own). Whether it keeps the quality of your work is for the evals to show: in a project's Evals tab, **Compare** your normal preset with a copy of it that routes.


**In the project's sandbox.** A project can choose **Sandbox** under *Where the agent works* in its settings ([install.md](install.md#a-sandbox-per-project)). Its conversations then run commands in a Debian container of that project alone instead of the environment's runner, with build tools, Python, Node.js and git, where the agent may install more with `sudo`. The project folder is the sandbox's `/workspace`, so file changes are ordinary project changes: they have checkpoints, the git tools work, and your permissions apply as in the container (the heading's project chip ends in *on Sandbox*). The sandbox has no network unless you tick *Network access* for the project; changing that makes the sandbox again on its next use. It starts by itself on the first command and keeps what was installed until it is rebuilt. Below the choice, the project's settings show the sandbox's state, CPU and memory, and when it stops: after an hour without use (not while a background process runs in the project); the next command starts it again. *Stop* stops it now. *Rebuild* replaces it with a fresh one from the current image, for example after updating YAAP or when an installation went wrong; the project folder stays, everything else in the sandbox is gone. Removing the project removes its sandbox.

**Approvals.** Every tool call shows in the chat. Calls that change something can ask first: approve or decline on the card. When you decline, you can say why in the card's *Reason* field (what to do instead, say): the agent gets the reason with the refusal, and YAAP's learning proposals read it (the project panel's Learning tab). What asks is set under Permissions, and per conversation with the **access** choice next to the input:

| Access | Meaning |
|---|---|
| As set | Your permission settings apply |
| Full | Everything runs without asking; pushing and commands with a secret still ask |
| Read only | Only reading tools run; everything else is refused |

After the agent read outside content (a web page, an attachment, a skill from GitHub), every changing action asks for the rest of that reply, so instructions hidden in that content cannot act on their own.

**Checkpoints.** Before each action that can change files, YAAP snapshots the project folder. Each such tool call has *Changes* (what it changed) and *Restore to before this*. The project panel's Checkpoints tab lists them per conversation. Git history is never touched.

**Goal mode** (in a project conversation). The target button beside the microphone puts `/goal ` in the input; type the objective after it. `/goal Make the test suite pass` keeps the agent working turn after turn until it marks the goal done or blocked, within a maximum number of turns and cost shown on the goal strip (pause, edit, stop). Typing `/` shows with `/goal` what earlier goals in the project cost with the same agent: usually (the median) and up to (the 90th percentile; with fewer than ten earlier goals, the most expensive one), from the 20 most recent; with fewer than three earlier goals there is no estimate yet. The goal strip repeats it while the goal runs.

### Other agents and outside clients

**Asking another agent.** Agents you add under Settings › Connections › *A2A agents* can be asked by YAAP's agent over the A2A protocol ("ask the research agent what changed in version 2"). Each call shows an approval card with the message that leaves YAAP; the answer counts as outside content, so after it every changing action asks for the rest of that reply. *Test* next to an agent reads its agent card, which shows that the address and token work without sending a message.

**YAAP for other agents.** With an API token for A2A, another agent can give YAAP tasks. Its agent card is at `/.well-known/agent-card.json` and it sends messages to `/a2a`, both with the token as `Authorization: Bearer <token>`. Each task becomes a conversation in the token's environment and project, titled *A2A · …* and marked unread; a follow-up in the same context continues that conversation. Your permissions apply as in the chat: what asks waits for you (the approval count in the header leads to it), and the other agent sees the task as *working* until you decide.

**YAAP's tools for an MCP client.** With an API token for MCP, an MCP client such as Claude Code, Codex or Claude Desktop can use YAAP's tools at `/mcp/outside`, confined to the token's environment and project. Calls that ask wait in YAAP: the header shows the count, and the token's conversation (*MCP client · token N*) shows the card. That conversation also lists every call the client made. See [configuration.md](configuration.md#yaap-for-other-agents-and-mcp-clients) for the client settings.

### The project rail

In a project (an open project conversation, or a new conversation in a project chosen in the sidebar) a column of
icons stands on the right edge of the chat. A click on an icon opens its panel beside the chat; a second click on the
same icon, or the panel's close button, closes it. The icons, top to bottom: **Changes** (with the number of changed
files), **Files**, **Branches and pull requests**, **History and checkpoints**, **Preview** (a green dot while a
process with a port runs), **Memory and learning** (with the number of open proposals), **Knowledge**, **Tasks**,
**Evals and reviews**, and **Project settings** at the bottom (also opened by the gear next to a project). The three
merged panels have a switch at the top: History | Checkpoints, Memory | Learning, Evals | Reviews. Outside a project
there is no rail.

In a window narrower than about 900 pixels the rail folds into one button at the top right of the chat (with a dot
when an icon has a count or a dot); it opens the rail and its panel as a drawer over the chat. A click beside the
drawer, **Esc** or its close button folds it again. Links that open a panel (`?panel=memory`, `?panel=checkpoints`,
`?panel=project`) keep working.

| Panel | Content |
|---|---|
| Changes | One list of the changed files, each with its status letter (M, A, D, R) and the lines added and removed; click a file to open its diff in place (a long diff shows its first 30 lines and *… N more lines*). Below: the commit message with ✦ to propose one, **Commit N files**, and **Sync · ↑1 · ↓0** (commits to push and to pull) with Fetch, Pull and Push in its menu; Sync stands out when there is nothing to commit but something to push or pull. **Review these changes** asks a model of another vendor; its findings show at the top of the list, with *Full report* to the Reviews tab. A project without a remote says so and links to Project settings |
| Files | The project's files where its conversations work (the project folder in the container, also for the sandbox, or the project's folder on a computer), as a tree with size and last change; search by name or content. `.git` and dependency folders (`node_modules`, `.venv`, `dist`, `build` and the like) are left out, as for the agent. Click a file to open it in the editor (below) |
| Branches | Local branches (create, switch, delete) and the GitHub pull requests: open one with its checks and diff, open a new one with a proposed title and description, merge (squash, merge or rebase) |
| History | Commits with their diff |
| Memory | The project memory (decisions, conventions, lessons) as entries to search, edit and forget; suggestions from the utility model to accept; *Tidy up* to merge duplicates (below) |
| Knowledge | Reference documents for the agent: uploads (PDF, Office, text, zip), web pages, or a folder of the project |
| Tasks | Scheduled tasks: unattended goal-mode runs on a cron schedule, at set moments or on demand, with their runs and, once a task has three finished runs, what a run usually costs and up to how much |
| History › Checkpoints | Snapshots per conversation, to compare and restore |
| Memory › Learning | Proposals from the work for the Blueprint and the memory, with their reasons, diff and measurement, to accept or decline; checks of the rules (below) |
| Evals › Reviews | Reviews of the project's changes by a model of another vendor (below), each with its findings beside the diff it reviewed |
| Project settings | Name, defaults for new conversations, running processes, where the agent works (with the sandbox's state, Stop and Rebuild), Git (the remote, with *Change*), context limit, compaction and the repository map, language, permissions, default skills, the project's keys and secrets, MCP servers, move to another environment, remove |

**Reviews by another vendor.** A model of another vendor than the one that wrote the code reads the changes and reports problems: per finding the file, the line, a severity (high, medium, low) and why. Nothing is applied; you or the agent change the code. A goal or scheduled task that changed files is reviewed by itself when it ends (one that changed nothing is not); start one yourself with **Review these changes** under Changes (the uncommitted changes). The heading's ⋯ shows the conversation's latest review (*Latest review · 3 findings (1 high)*, *Skipped*); choose it to open the Reviews tab on it, where the findings sit above the diff and a finding shows its file's diff. The reviewer is the utility model for *Code reviews* (Settings › Models › Utility and workers, utility tasks) when that is of another vendor, else the cheapest model of another vendor that the model policy, your keys and the budget allow. When none is allowed (for example a policy that allows only one vendor, or no key of a second vendor), the review is *Skipped* and says why. The review sees the diff (a large one is reviewed per file, up to a limit, with a note on what was shortened or left out), the goal or first message, and the project's standards; secrets go as placeholders, and the request is checked and logged like any other model call. Its cost shows in the usage as the utility task *review*.

**Tidying up the memory.** *Tidy up* in the Memory tab lets the utility model look for entries that say the same or overlap, and propose merges. Each proposal shows the entries it replaces and the merged text: *Merge* (after an *Edit* if you like) replaces them by the merged entry in one commit of `memory.md`, so it can be undone from the History tab like any memory change; *Keep apart* leaves them as they are. Nothing changes until you accept a merge. YAAP also tidies up by itself after every 20 new entries; its proposals wait in the same list. A proposal whose entries you changed or removed meanwhile is dropped.

**Learning from the work.** The Learning tab lists what YAAP proposes after *Learn from this*, or by itself after a goal or a scheduled task that had declined actions, tests that failed twice or searches for what the Blueprint already says. A proposal is a rule for a standard at the global, environment or project level, a line in the project memory, or a missing step in a skill, with why and the steps it rests on; *Diff* shows the change. A Blueprint proposal waits on a branch of its layer (`yaap-learning/<number>`, which the project's Branches tab shows for a project rule), so nothing in the layer changes yet. **Measure** plays the project's eval cases with and without it, three times each (the runs appear in the Evals tab too), and shows the pass rates and the per-case comparison when both are done. A project without eval cases shows *not measured*; you can still accept. **Accept** writes the change as one commit (a memory line as one memory commit) and removes the branch; **Decline** removes the branch and changes nothing. Under *Check the rules*, **Measure without it** proposes removing one rule and measures whether it makes a difference (equal pass rates: it does not, and accepting removes it); **Find contradictions** lets the utility model look for rules of different levels that contradict each other, as a report. Without a utility model, YAAP still proposes the plain cases: a declined action with your reason becomes a project rule, a test that failed twice a memory line.

**The editor.** A file opened from the Files tab shows in an editor with syntax colours; the panel widens while it is open. Save with the button or **Ctrl+S**. YAAP refuses the save when the file changed after you opened it (by the agent or anyone else) and offers to reload it, so nobody's changes are lost unseen; the agent likewise has to read a file again after you saved it. In the container every save takes a checkpoint first, so it shows in Checkpoints and can be rolled back; on a computer over SSH it cannot. Images and PDFs are shown, not edited. Text over 1 MB, or not in UTF-8, opens read-only; files over 10 MB do not open.

**A document beside the chat.** A Markdown file can be open beside a project conversation, to write it together with YAAP: a plan, notes, a README. Open it with *Open beside the chat* in the editor of the Files tab, or ask YAAP to open one (it can also start a new one). The panel shows the document rendered or as its source; edit the source and save with the button or **Ctrl+S**, with the same check and checkpoint as the editor. Each conversation has one document; opening another replaces it, and the close button closes it (the file stays).

You and YAAP take turns. While YAAP changes the document, the panel is read-only and follows each change; when the reply ends it shows what YAAP changed as a diff, with **Accept** and **Roll back**. Roll back puts the document back as it was before YAAP's change (or removes a document YAAP created); in the container it takes a checkpoint first, so even the roll back can be undone in Checkpoints. Until you choose, you cannot edit; text you had not saved is kept aside and *Put them back* restores it afterwards. Your saved edits reach YAAP with its next step, as the changes you made, also while it is working; YAAP cannot write the document over an edit of yours it has not seen, and you cannot save over a change of YAAP's that the panel did not show you.

### The preview

The rail's **Preview** icon in a project conversation opens the project's app beside the chat; it has a green dot while
a process with a port runs. Like every panel of the rail it takes the place of the open one. Ask the agent to start
the dev server with its port (for example "start the dev server on port 5173"); the agent's `start_process` records the
port, and the panel lists every running process of the project that has one. Pick one in the list to show it.

- The app is shown on the preview's own address (`http://localhost:8081` by default, see
  [configuration.md](configuration.md#the-preview-port)), as if you opened the dev server itself: its links,
  scripts and forms work, and so does the hot reload of Vite and similar dev servers (their WebSocket is passed on).
- After every file the agent writes or edits, the panel reloads by itself. **Reload** does it by hand.
- **Screenshot** takes a picture of the app through the agent's own browser, in the runner beside the dev server, and
  shows it under the frame; it is kept with the conversation's other screenshots. This works for processes in the
  runner; a project with its own sandbox or on a computer shows the app, but the agent's browser cannot see it.
- The preview opens with a link that works once and for two minutes, then a cookie of the preview's own keeps it open
  while the process runs. A process that stops leaves the list; once the agent starts it again, pick it there.
- The app never gets YAAP's sign-in: its pages cannot change anything in YAAP, and YAAP's cookies and tokens are
  taken out of every request before it reaches the dev server (see [SECURITY.md](../SECURITY.md)).

### Projects

A project is a folder under the environment's workspace, empty, cloned from GitHub (with the environment's GitHub token), or a new repository made from a GitHub template repository. Mark a repository as a template in Settings › Git and GitHub; *New project* then offers it. Its `.blueprint/` folder holds the project's own Blueprint layer and `memory.md`.

## Blueprint

The Blueprint is what every conversation follows: Markdown files in git, at the levels global, environment and project. A later level overrides, extends or switches off what it inherits.

| Kind | Purpose |
|---|---|
| Instructions | General instructions, always in the prompt |
| Standards and engineering | One file per category (coding, testing, security, …). A summary and up to seven rules are always in the prompt; the full text is loaded when the agent needs it. |
| Skills | Reusable ways of working, loaded on demand or pinned |
| Commands | Prompt templates for `/name` |
| Agents | Role, tools, skills and model per agent (developer, reviewer, …), with a Runs tab that lists their runs, newest first, 50 at a time (*Load more* for older ones); *Trace* on a run opens its steps and delegated sub-runs |

- **New** creates an item; **Import** turns existing Markdown documents (or a zip of them) into items, with proposals you review first; **Skill from GitHub** installs a skill from a link, after a review, pinned to a commit, with updates shown as a diff; **Add from a package** reads a GitHub repository with a `blueprint-package.json` ([format](blueprint-packages.md)), shows its standards, skills, commands and agents for review, and installs the ones you pick into the level you choose, pinned to a commit. Items with scripts or binary files are refused. An installed item shows its package and commit; **Check for updates** shows each item's changes since then as a diff, and nothing changes until you accept the items you want.
- Each item has **Edit**, **History** (its commits) and **Used in prompt** (exactly what it adds).
- The rail shows each layer's sync state; **Push** and **Set remote** keep a layer in a repository of your own.
- **Matrix** shows all items against all levels.

A new installation starts with an empty Blueprint; it is yours and stays on your computer.

### Eval cases

An eval case is a task with a known good outcome, used to measure the agent: one Markdown file per case in the project's Blueprint layer, `.blueprint/evals/<name>.md` (lowercase letters, digits and hyphens), versioned with the project. The front matter says where the case starts and how it is judged; the body is the prompt the agent gets.

```markdown
---
summary: Fix the ISO week bug in the date parser
start:
  commit: 3f2a1bc            # or checkpoint: 42, for a case made by replay
agent: developer             # optional, an agent of the Blueprint
preset: Balanced             # optional
allowed_tools: [read_file, edit_file, run_command]   # run without asking; other calls that ask are refused
budget:                      # optional; every value needs its unit
  steps: 40 steps
  tokens: 200k tokens        # also 1.5M tokens or 900 tokens
  cost: 0.50 USD             # or $0.50
  time: 10 min               # s, min or h
checks:
  - kind: command            # must exit with 0
    run: pytest -q tests/test_dates.py
    timeout: 5 min           # optional, default 10 min, at most 1 h
  - kind: file               # content that must or must not match
    path: src/dates.py
    contains: def parse_iso_week       # text, or a list of texts
    not_matches: '^\s*print\('         # regular expression (also matches, not_contains)
  - kind: file
    path: src/old_dates.py
    exists: false            # the file must be gone
  - kind: rubric             # scored by a model
    name: explains the cause # optional, for every check
    rubric: The reply names the cause of the bug and the test that covers it.
    files: [src/dates.py]    # optional, shown to the scorer
---

ISO week 53 is parsed as week 1 of the next year. Fix the parser and keep the tests green.
```

- A case needs a start state (a commit hash, not a branch name, or a checkpoint number), a prompt, and checks, a budget, or both. Paths are relative to the project, without `..` or `.git`.
- A bare number such as `steps: 40` is refused: write the unit. Unknown keys and unknown check kinds are refused too, with every problem of the file listed at once.
- A case passes when every check passes. A rubric is scored by the utility model, or by a model of another vendor than the one under test when the model policy allows one; what scoring costs is shown apart from the case's own cost.
- **Save as eval case**, under the last reply of a conversation in a project or in its heading's ⋯, makes a case of a conversation that went well. It proposes the conversation's first message as the prompt, the project's commit just before that message as the start (or the conversation's first checkpoint when there is no such commit; the saved case keeps that checkpoint, which would otherwise be removed after 30 days), twice what the conversation used as the budget, and checks from the utility model: the project's test command and the files the conversation changed. Without a utility model you get standard checks (the test command, removed files, and a rubric over the changed files). Edit everything in the dialog and choose **Save case**: the case is written to `.blueprint/evals/<name>.md` and committed in the project, as one commit. A name the project already has is refused.

### Eval runs

The project panel's **Evals** tab lists the project's cases, with what is wrong with an invalid one. **Run all** plays every valid case, **Run** one case; `/eval` in a project conversation does the same (`/eval <case>` for one), and a scheduled task can do it on a schedule (*What a run does* › *Run the project's eval cases*).

- Each case plays in a throwaway copy of the project at its start commit (or checkpoint), under `.evals/` in the environment's folder; the project itself is not touched. Commands run in the environment's runner, which can reach the internet unless *Runner network* is off in Settings › Targets.
- Nobody approves during a run: a call that would ask for approval is refused and counted as *refused as asked*. List the tools a case may use without asking in its `allowed_tools`.
- A case that goes over its budget is stopped at once and fails. Without a time budget a case still stops after two hours.
- A run shows per case pass or fail, each check, cost, tokens, steps, time and a link to the conversation the case played in (kept archived, out of the sidebar). A passed case's copy is removed; a failed one is kept for 24 hours to look at.
- One case plays at a time per environment; the setting *Eval cases at a time* allows up to four.
- **Compare** plays the cases in your normal setup and in a variant, three times each by default: another preset, another agent, or a Blueprint layer at a git branch (a proposed rule, say), read without switching the layer's branch. Or pick any two runs. The table shows per case the verdict, the pass rate over the plays, the median cost, and how cost, tokens and steps differ (B minus A).
- Removing a run removes the conversations its cases played in.

### The baseline eval set

YAAP's repository holds a fixed eval set to measure YAAP itself: ten cases on *Stockroom*, a small Python and TypeScript project (`evals/baseline/`). A change that should make YAAP cheaper is compared with a run of it before and after.

1. **Make the project.** Create a new, empty project named *Stockroom* (*New project*, empty). On the computer that runs YAAP, from `backend/` in YAAP's folder, build the baseline into its folder, and copy the issue tracker MCP server next to it:

   ```sh
   python -m app.scripts.build_baseline_project ../data/workspace/<environment>/stockroom --mcp-folder ../data/workspace/<environment>/baseline-mcp
   ```

   `<environment>` is the environment's folder name. The script needs YAAP's Python environment (the `backend/.venv` of a development checkout) and git. It prints the commits; the cases start at them. Instead you can build it in any empty folder, push it to a private GitHub repository and clone that as the project.
2. **Add the tracker.** In the project's settings, *MCP servers*, add a server named `tracker` (the name matters: one case allows `tracker__get_issue` and its siblings) with transport *stdio*, command `python3` and argument `/workspace/baseline-mcp/tracker_server.py`. It needs no network or key; its changes are forgotten when it stops.
3. **Run it three times.** In the project panel's **Evals** tab the ten cases appear; choose **Run all** three times (or **Compare** with no variant change). Keep the preset you normally use, and run before any change you want to measure.
4. **Record the baseline**: per run the pass rate, the tokens and the cost per case and in total, and the median of the three runs per case. Keep the run ids; **Compare** shows any later run against them.

Later, each efficiency change is measured the same way: three runs of the set on the new version, compared with the baseline runs in **Compare** (pass rate, median cost, tokens and steps, B minus A). A change is kept only when the pass rate holds. Rubric checks are scored by the utility model and cost a little on top, shown apart.

Developers also measure what YAAP sends without a model: `python -m app.scripts.token_footprint` (from `backend/`) prints the characters and estimated tokens of the system parts, the tool definitions and the first tool results for a plain chat, a project conversation and a goal turn.

## Search

**Ctrl K** searches conversations, project memory, knowledge and the Blueprint of the active environment while you type, in Dutch and English, by words and by meaning. The Search page shows all results with filters.

## Settings

| Section › tab | Content |
|---|---|
| **Account** (Global) | |
| › Profile | Display name, time zone |
| › Access | Change the password, sign out (also every other browser), and API tokens |
| **Connections** | |
| › Providers | How YAAP reaches Claude and OpenAI, the default API keys (with a link to the Vault), Ollama, the search index |
| › Vault | Every key and secret: several keys per kind, expiry dates, where each is used and its use log; at Global also the master key |
| › MCP servers | Per environment: outside tools for the agent, each with a test, its tools and its log |
| › A2A agents | Per environment: other agents YAAP's agent may ask, each with a test |
| **Models** | |
| › Presets | Model presets with their roles and the longest reply, and the preset for chats outside a project (per environment; empty: the default preset). Under each preset: *Route each reply by the kind of message* (below) |
| › Utility and workers | The workers for delegated tasks, the utility model and a model per utility task (for example a local model for titles and commit messages; the outside-content check always uses the utility model), the embedding model |
| › Context | The context limit, when YAAP shortens old tool output and summarises, automatic compaction on or off, and the repository map on or off |
| › Policy | Per environment: which connections, models and A2A agents may be used, whether a model must be marked for business data processing, and patterns that block a request before it leaves YAAP. A project narrows it in its own settings. |
| **Agent** | |
| › Permissions | Tool permissions, command and website allowlists, the outside-content check |
| › Default skills | Skills pinned in every new conversation |
| › Language | The reply language and the work language |
| **Git and GitHub** | Commit author, and every repository with its local and remote state |
| **Targets** (environment) | The environment's runner and whether it can reach the internet, and computers the agent can work on over SSH, such as this one: setup script, host key, folders, full access |
| **Usage and audit** | |
| › Usage and budget | Cost and subscription value per month, model, project and role; monthly budgets |
| › Audit log | Per environment: every model request that left YAAP, which model got which kinds of data, exported as CSV or JSON |
| › Telemetry | Each reply as an OpenTelemetry trace, sent to an endpoint or downloaded per period |
| **Environments** (Global) | Add and rename environments |
| **Environment** (in an environment) | The environment's name and colour |
| **System** (Global) | |
| › Backup | Backups and restore |
| › Plugins | Your own plugins in `data/plugins`, switched on one by one |

Global has eight sections and an environment seven; a tab that exists only in an environment is left out at Global. Each
tab has its own address, such as `/settings/connections/vault`, and older addresses
(such as `/settings/vault`) lead to their new place. The level switch at the top chooses Global or an environment; an
overridden value shows what it overrides, with a reset, and the rail and the tabs count the overridden values.

### Access

YAAP asks for its password before it shows anything; *Sign out* is at the top right. **Settings › Account › Access** (Global) changes the password, signs out every other browser, and makes **API tokens**: give the token a name and a purpose (REST API, MCP server or A2A agent; the last two confined to an environment and optionally a project), copy it when it is shown (YAAP shows it once), and *Revoke* it when the program no longer needs it. See [configuration.md](configuration.md#access-password-and-api-tokens).


### Plugins

**Settings › System › Plugins** (Global) lists every folder in `data/plugins` with its version, description and what it
registers (tools, model providers, kinds of keys), read from its `plugin.json` without running any of its code. A
plugin is Python code that runs inside YAAP with YAAP's rights, so it is off until you choose **Switch on…** and
confirm. Switching on or off takes effect after a restart: **Restart YAAP now** appears while a change waits (replies
in progress stop). A plugin that fails to load is switched off and shows its error; YAAP starts without it. A plugin
with a page gets **Open page**, which shows the page in an isolated frame. Its panel says which of YAAP's API routes
the page may call (*Its page may call: …*); YAAP makes those calls for the page with your session and refuses all
others, and the page follows the active environment, project and theme. How to write one: [plugins.md](plugins.md).

## Backup and restore

**Settings › System › Backup** makes one zip in `data/backups` with the database, the Blueprint, attachments, knowledge, plugins and the project folders (without rebuildable folders such as `node_modules`): every day at 03:00 by default, and by hand with **Make backup now**. The last seven are kept. Each backup is also copied to the destinations you add (a folder on another disk or a network share, see [configuration.md](configuration.md#backups)); **Recent runs** shows how each went. Download a backup to keep it elsewhere; **Upload backup** adds one made elsewhere.

**Restore…** checks the backup (that it is a YAAP backup, not from a newer YAAP, and readable with your master key), asks you to type its name, makes a fresh backup of the current state, and restarts YAAP with the backup in place. The replaced data stays in `data/.restore-previous` until the next restore. Reinstall project dependencies afterwards (for example `npm install`), and sign in to Claude Code and Codex again.

Not in a backup: the master key and the Claude Code and Codex sign-ins.

### By hand, when YAAP does not start

1. Stop YAAP: `docker compose down`.
2. Move the data folder aside (for example to `data-old`) and create an empty `data` folder.
3. Unpack the backup zip into `data`, so that `data/yaap.db`, `data/blueprint`, `data/files` and `data/workspace` exist again. `manifest.json` can be removed.
4. Make sure `.env` has the same `YAAP_MASTER_KEY` as when the backup was made.
5. Start YAAP: `docker compose up -d`. Migrations bring an older database up to date.

## Usage and budgets

Every model call is recorded with its tokens and cost. Calls through a subscription (Claude Code, Codex) cost nothing per call; their value is shown as what the same tokens would cost through the API. Budgets stop API-key use for the rest of the month when reached; the sidebar shows them with a meter. A reply that reaches a budget while it works stops right after the model call that reached it, as if you pressed stop: the reply says why, a goal ends as *Limit reached* with the budget as its reason, and what the reply used counts towards the month.

**The one-hour prompt cache.** With an Anthropic API key, YAAP keeps the fixed part of the prompt (instructions, tools, the Blueprint) cached for an hour instead of five minutes during goals, scheduled tasks and conversations where you replied more than five minutes after the previous answer. Writing that cache costs more (twice the input price instead of 1.25 times), but each later message within the hour reads it cheaply instead of writing it again. Quick follow-ups keep the five-minute cache. A model step that used the hour says *Prompt cache: 1 hour* in the trace. Claude Code and Codex manage their own cache.

## The audit log

**Settings › Usage and audit › Audit log** (per environment) lists every model request that left YAAP: replies, sub-runs, coding agents as workers, utility tasks (by name: title, commit message, summary, …), calls to remote A2A agents and traces sent to an OpenTelemetry collector. Each row shows the time, the project and conversation, the model and connection, and which kinds of data went along: the conversation, files and tool output, knowledge, project memory, attachments and secrets. Secrets appear only by their placeholder names, never their values, and no prompt or reply is stored. A request a check refused before it left is marked *refused* with the reason.

Filter by period, project, model and purpose; *Export CSV* and *Export JSON* download the rows that match. Rows are kept for a year. Embeddings are made by the local model and never leave YAAP, so they are not listed.

## OpenTelemetry export

**Settings › Usage and audit › Telemetry** (at Global, or per environment to override) exports each reply as an OpenTelemetry trace in OTLP JSON: the reply and its sub-runs, every model call with its model, tokens, cost and duration, and every tool call with its approval and whether it failed. The traces follow the GenAI semantic conventions (OpenTelemetry semantic conventions v1.37.0), so Jaeger, Grafana Tempo, Langfuse, Phoenix and other OTLP tools can read them.

- **OpenTelemetry endpoint**: the address of an OTLP/HTTP collector, such as `http://otel-collector:4318`. After each reply YAAP posts its trace to `<endpoint>/v1/traces` in the background. A collector that is down or refuses is noted in YAAP's log; the reply is never held up. Empty: nothing is sent.
- **OpenTelemetry headers**: one `Name: value` per line, for example `Authorization: Bearer {{secret:OTLP_TOKEN}}`. A value names its secret from the vault as a placeholder; store the token in **Settings › Connections › Vault** as a secret for the agent. The value is filled in when sending and never stored in the setting.
- **Include prompt content**: off by default, so a trace holds only names, times, tokens and costs. On, a model call also carries what the model saw (the system prompt and the messages, with secrets as placeholders) and a tool call its arguments; such a trace is checked against the model policy's patterns and the known secret values before it leaves, and not sent when it matches. The setting applies to downloads too.
- **Download**: the replies that started in a period (at most 1,000), one trace each; at Global for all environments, in an environment for its own. A single reply can be downloaded from `/api/v1/telemetry/runs/<run id>/otlp`.

Every trace sent (or stopped by the check) is listed in the audit log as *OpenTelemetry export*, with the collector's address.
