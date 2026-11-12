# YAAP compared with other tools

Checked on 2026-11-12 against each tool's own documentation. Tools change; check the sources at the bottom before you decide. "Unclear" means the tool's own pages that were read did not settle it; "Not documented" means its documentation was searched and has no such feature. This page states facts and makes no claim about which tool is better.

YAAP is one person's AI development environment in a browser, self-hosted in Docker. The other tools differ in what they are built for, so some rows only apply to some of them.

| | YAAP | Claude Code | Codex CLI | Cursor | OpenHands | Open WebUI |
|---|---|---|---|---|---|---|
| **What it is** | Browser workbench with a coding agent | Coding agent | Coding agent | AI editor and coding agent | Browser client and SDK for coding agents | Self-hosted chat platform |
| **Runs where** | Docker on your computer, on `127.0.0.1` | Your computer; also desktop app and web (Anthropic's cloud) | Your computer; also desktop app and Codex Web | Cursor's editor app on your computer | Your computer (npm or Docker), or OpenHands Cloud | Your server or computer (pip, Docker, desktop app) |
| **Model vendors** | Claude, OpenAI, Ollama and other OpenAI-compatible servers | Anthropic API, Amazon Bedrock, Google Vertex, Microsoft Foundry, gateways; local models not mentioned | OpenAI by default; custom providers, Ollama and LM Studio | Several vendors, listed in its model table | "Any LLM" | Ollama, OpenAI-compatible APIs, Anthropic |
| **Local models** | Yes, through Ollama | Anthropic does not support routing it to non-Claude models through a gateway | Yes (`--oss`) | Not documented | Yes (Ollama, LM Studio, SGLang, vLLM) | Yes, through Ollama |
| **Interface** | Web | Terminal, VS Code, JetBrains, desktop, web | Terminal, IDE extension, desktop, web | Editor, terminal CLI | Web, CLI (no longer maintained) | Web |
| **Agent works in your projects** | Yes | Yes | Yes | Yes | Yes | Not as a project agent; an optional terminal tool gives the AI a filesystem |
| **Approval per action** | Yes, with access levels per conversation | Yes (permission modes) | Yes (`on-request`, `never`, granular policy) | Yes; run modes decide when it asks | Yes, in the SDK (confirmation policies `AlwaysConfirm`, `ConfirmRisky`) | Unclear |
| **Checkpoints and rollback** | Checkpoint before every change | Checkpoints per prompt, `/rewind`; Bash-only changes not tracked | An undo action exists; its docs advise git for rollback | Checkpoints of the agent's changes; files only | Unclear | No |
| **Isolation of commands** | Separate runner container, optional sandbox per project | Optional OS sandbox (Seatbelt, bubblewrap); off by default; not on native Windows | OS sandbox modes: read-only, workspace-write, full access (Seatbelt, bwrap and seccomp, Windows sandbox); no Docker needed | Sandbox for terminal commands (see its run modes) | Optional Docker sandboxes; the npm install has none | Optional Docker for the terminal tool |
| **Instructions as files in git** | Blueprint: Markdown in git, also standards and skills | `CLAUDE.md`, skills, hooks; reads `AGENTS.md` | `AGENTS.md` | Rules, skills | `AGENTS.md`, `CLAUDE.md`, skills in `.agents/skills/` | No |
| **Evals** | Eval cases per project, compared between runs | Not documented | Not documented | Not documented | Not documented | Not documented |
| **Trace and replay** | Trace of every reply; replay from a model call | OpenTelemetry traces (beta, off by default); no replay documented | OpenTelemetry export (off by default); replay unclear | Not documented | Not documented | Not documented |
| **Several users on one server** | No, one person | Not as a server | Not as a server | Teams plans | Cloud and Enterprise | Yes, with roles and groups |
| **IDE plugin** | No | VS Code, JetBrains | VS Code, Cursor, Windsurf | It is the editor | Not documented | No |
| **Needs Docker** | Yes | No | No | No | Only for the sandboxed modes | Not required (pip works) |
| **Licence** | MIT | Proprietary: "All rights reserved", Anthropic's terms | Apache-2.0 | Proprietary: owned by Anysphere, no reverse engineering (terms of service) | MIT (main repository) | Own licence with a branding clause, stated not OSI-approved |

## What YAAP does not have

- No server for several users or a team: one password, one person, one computer.
- No IDE plugin: it is a browser interface beside your editor.
- Docker is required, including for the install.
- A model API key or a Claude or ChatGPT subscription is needed; there is no model of its own.

## Notes

- **Claude Code** sandboxing is off until you turn it on, and Docker is not needed for it. Checkpoint snapshots are removed after about 30 days by default.
- **Codex CLI**: the sandbox and approval policy are set separately; the older `untrusted` approval policy is retired.
- **Cursor** bring-your-own-key is mentioned for Teams and Enterprise pricing; the Plus plan excludes it.
- **OpenHands** documents the CLI and the older local GUI as no longer maintained; Agent Canvas is the maintained client. Licences are per repository.
- **Open WebUI** has no coding agent working in projects; its Open Terminal gives the AI a terminal and files.

## Sources

- Claude Code: <https://code.claude.com/docs/en/overview.md>, <https://code.claude.com/docs/en/checkpointing.md>, <https://code.claude.com/docs/en/sandboxing.md>, <https://code.claude.com/docs/en/model-config.md>, <https://code.claude.com/docs/en/llm-gateway.md>, <https://code.claude.com/docs/en/monitoring-usage.md>, <https://github.com/anthropics/claude-code/blob/main/LICENSE.md>
- Codex CLI: <https://github.com/openai/codex>, <https://learn.chatgpt.com/docs/config-file/config-advanced>, <https://learn.chatgpt.com/docs/agent-approvals-security>
- Cursor: <https://cursor.com/docs>, <https://cursor.com/docs/agent/chat/checkpoints>, <https://cursor.com/docs/cli/overview>, <https://cursor.com/docs/models-and-pricing>, <https://cursor.com/docs/agent/terminal>, <https://cursor.com/terms-of-service>
- OpenHands: <https://docs.openhands.dev/overview/introduction>, <https://github.com/OpenHands/OpenHands>, <https://docs.openhands.dev/sdk/guides/security>, <https://docs.openhands.dev/overview/skills>, <https://docs.openhands.dev/openhands/usage/llms/llms>
- Open WebUI: <https://docs.openwebui.com/>, <https://docs.openwebui.com/license>, <https://docs.openwebui.com/features/>
- YAAP: [usage.md](usage.md), [concepts.md](concepts.md)
