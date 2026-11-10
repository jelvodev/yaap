# Changelog

What changed in each release, newest first. Every release is tagged (`v0.5.7`); update by checking out or pulling
the release, then rebuilding (see [Updating](docs/install.md#updating)). **Action needed** lists what you must do
yourself when you update; it says "None." when there is nothing.

## Unreleased

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
  time you press a sign-in button (about 700 MB, a few minutes; the card shows *Installing Claude Code…*), then starts
  the sign-in. The versions and checksums are pinned per release in `tools/package-lock.json`, so a release that pins
  newer versions installs them again at its next start, and Claude Code is downloaded by your own YAAP, never shipped
  in an image of ours. Before the install, a reply or a worker that needs one of them says it is not installed yet
  and points to Settings › Connections › Providers. The monthly refresh is gone: the update script no longer sets
  `CLI_REFRESH`. `data/tools` is not in backups.

### Action needed

If you use Claude Code or Codex: after updating, open Settings › Connections › Providers and press its sign-in
button once. That installs both programs from npm (about 700 MB); an existing sign-in is kept, so you are not asked to
sign in again. Until then they show *not installed yet*. On a computer without access to `registry.npmjs.org` they
cannot be installed.

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
