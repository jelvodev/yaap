# YAAP in five concepts

YAAP has a lot in it, but five ideas carry all of it. Read them once and the rest of the interface falls into place.

## 1. Environments

An environment is a separate world, such as *Private* and *Work*. Each has its own API keys, projects, standards and
budget, and never sees another's. YAAP starts with *Private* and *Work*; rename them in the first start or
later in Settings.

Settings work at three levels: **Global**, then the **environment**, then the **project**. A lower level overrides a
higher one, so you set something once and change it only where it differs.

## 2. Projects

A project is a folder with your code, usually a git repository, plus the conversations, memory and knowledge that
belong to it. YAAP keeps the folder in its data folder (`data/workspace`), so you can also open it in your own editor.

Conversations outside a project are fine for questions; inside a project the agent can read, change and run its code.

Each project also has a board of cards, from an idea to finished work, kept in the project's own git repository or a separate private one. A plan on a card can be built in a copy of the project and reviewed, and every project keeps a short *Current state* in its memory, so a new conversation starts where the last stopped (see [Using](usage.md#the-board-and-builds)).

## 3. The Blueprint

The Blueprint is how you want the agent to work, written down once: instructions, standards, skills, commands and
agents, as Markdown files in git. It has the same three levels (global, environment, project), and every conversation
follows it. YAAP's starter is a ready-made Blueprint for a careful, test-first way of working that you can install
and change.

## 4. The runner

Everything the agent runs, from tests to a dev server or a browser, runs in the **runner**: a container per
environment with common build tools, which holds that environment's projects and cannot reach YAAP's own keys or
database. Risky actions wait for your approval, and every change has a checkpoint you can roll back.

A project can choose a stricter **sandbox** (only its own folder, no network unless you allow it), or a folder on your
own computer over SSH.

## 5. Connections

A connection is how YAAP reaches a model: your Claude or ChatGPT subscription (through Claude Code and Codex), an API
key from Anthropic or OpenAI, Ollama, or another OpenAI-compatible server. You can use several side by side and pick
the model per conversation.

YAAP uses a model in three roles: the **main** model you chat with, a cheaper **worker** model for delegated work, and
a small **utility** model for titles, commit messages and summaries. Presets set all three at once.

## Next

- [Installing](install.md): setup, connecting a model, updating.
- [Using](usage.md): a tour of the chat, projects, the Blueprint and Settings.
- [Configuring](configuration.md): levels, connections, permissions and more.
