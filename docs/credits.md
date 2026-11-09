# Credits

YAAP's starter Blueprint ([`blueprint/starter/`](../blueprint/starter)) is written new for YAAP. Its standards,
skills, commands and agents draw on the ideas, methods and checklists of four open projects, credited here. Nothing
was copied from them: every item was written again in plain words, shorter, around YAAP's own tools, and every
example was written new and checked. Each pack also has a `NOTICE.md` with the sources it draws on.

## Sources

### obra/superpowers

- **Repository:** [github.com/obra/superpowers](https://github.com/obra/superpowers), and the archived
  [github.com/obra/superpowers-skills](https://github.com/obra/superpowers-skills).
- **License:** MIT, Copyright (c) 2025 Jesse Vincent.
- **What we took:** the working method for a coding agent: agreeing on a design before building, writing and
  working through a plan, test-driven development, systematic debugging, evidence before calling work done,
  asking for and receiving a code review, finishing a branch, and writing skills.

### msitarzewski/agency-agents

- **Repository:** [github.com/msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents).
- **License:** MIT, Copyright (c) 2025 AgentLand Contributors.
- **What we took:** the checklists and rules of its roles: minimal changes, code review, onboarding to a codebase,
  technical writing, security architecture and secrets, test automation, accessibility audits, a finish gate for
  user interfaces, data engineering, database reliability, incident response, site reliability, DevOps, product
  management, sprint prioritisation and prompt engineering. The personas, emoji and stack-specific agents were left
  out.

### wshobson/agents

- **Repository:** [github.com/wshobson/agents](https://github.com/wshobson/agents).
- **License:** MIT, Copyright (c) 2024 Seth Hobson.
- **What we took:** patterns and checklists from its skills and commands: error handling, code review, debugging,
  the test-driven cycle, architecture decision records, changelogs, TypeScript types, React state, end-to-end
  tests, Python testing and typing, uv, API design, FastAPI, PostgreSQL tables, migrations, SQL tuning, dbt, Airflow,
  data quality, GitHub Actions, secrets management and Kubernetes manifests. Code samples were not taken over; the
  examples are new.

### anthropics/skills

- **Repository:** [github.com/anthropics/skills](https://github.com/anthropics/skills).
- **License:** Apache License 2.0, per skill (each skill used has its own `LICENSE.txt`), Copyright 2026 Anthropic,
  PBC.
- **What we took:** the approach of the skills `skill-creator`, `frontend-design`, `webapp-testing` (as text only),
  `mcp-builder`, `internal-comms` and `discernment-nudge`. Only skills with a license of their own were used. The
  `doc-coauthoring` skill has none, so nothing was taken from it; YAAP's own document skills share only its general
  idea. Skills that need scripts or binary files were left out, because a Blueprint package is text only.

## Adaptations under the Apache License 2.0

The starter items adapted from a skill in anthropics/skills are derivative works under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0). They keep that license (`license:
Apache-2.0` in their front matter), and the last line of each says that it was adapted, from which skill, and what was
changed. The `NOTICE.md` of their pack repeats the notice. All other starter items are published under YAAP's MIT
License.

## Per item

Every item names its sources in its front matter: `sources` for standards, engineering standards, commands and
agents, `metadata.sources` for skills, as `<repository>/<path>` or `own` for YAAP's own work.

<!-- The table of every item and its sources is generated from that front matter. -->
*The table per item follows here; it is generated from the items' `sources` front matter.*
