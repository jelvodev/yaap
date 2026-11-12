# Credits

YAAP's starter Blueprint ([`blueprint/starter/`](../blueprint/starter)) is written new for YAAP. Its standards,
skills, commands and agents draw on the ideas, methods and checklists of four open projects, credited here. Nothing
was copied from them: every item was written again in plain words, shorter, around YAAP's own tools, and every
example was written new and checked. Each pack also has a `NOTICE.md` with the sources it draws on.

## Sources

### obra/superpowers

- **Repository:** [github.com/obra/superpowers](https://github.com/obra/superpowers).
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
changed. Each carries the full license text as `LICENSE.txt` in its folder, and the `NOTICE.md` of their pack
repeats the notice. All other starter items are published under YAAP's MIT License.

## Per item

Every item names its sources in its front matter: `sources` for standards, engineering standards, commands and
agents, `metadata.sources` for skills, as `<repository>/<path>` or `own` for YAAP's own work.

### Core (`yaap-core`)

| Kind | Item | Sources |
|---|---|---|
| Standard | `working-method` | [agency-agents: engineering-minimal-change-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-minimal-change-engineer.md), [superpowers: verification-before-completion](https://github.com/obra/superpowers/tree/main/skills/verification-before-completion), own |
| Standard | `security` | [agency-agents: security-secrets-credential-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/security/security-secrets-credential-engineer.md), [agency-agents: security-architect.md](https://github.com/msitarzewski/agency-agents/blob/main/security/security-architect.md), own |
| Standard | `testing` | [superpowers: test-driven-development](https://github.com/obra/superpowers/tree/main/skills/test-driven-development), [agency-agents: testing-test-automation-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/testing/testing-test-automation-engineer.md), own |
| Standard | `git` | [agency-agents: engineering-git-workflow-master.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-git-workflow-master.md), [wshobson: git-workflow.md](https://github.com/wshobson/agents/blob/main/plugins/git-pr-workflows/commands/git-workflow.md), own |
| Standard | `documentation` | [agency-agents: engineering-technical-writer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-technical-writer.md), own |
| Engineering | `error-handling` | [wshobson: error-handling-patterns](https://github.com/wshobson/agents/tree/main/plugins/developer-essentials/skills/error-handling-patterns), own |
| Engineering | `code-review` | [wshobson: code-review-excellence](https://github.com/wshobson/agents/tree/main/plugins/developer-essentials/skills/code-review-excellence), [agency-agents: engineering-code-reviewer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-code-reviewer.md) |
| Skill | `brainstorm-design` | [superpowers: brainstorming](https://github.com/obra/superpowers/tree/main/skills/brainstorming), own |
| Skill | `write-plan` | [superpowers: writing-plans](https://github.com/obra/superpowers/tree/main/skills/writing-plans), [agency-agents: project-manager-senior.md](https://github.com/msitarzewski/agency-agents/blob/main/project-management/project-manager-senior.md), own |
| Skill | `execute-plan` | [superpowers: executing-plans](https://github.com/obra/superpowers/tree/main/skills/executing-plans), [superpowers: subagent-driven-development](https://github.com/obra/superpowers/tree/main/skills/subagent-driven-development), own |
| Skill | `test-driven-development` | [superpowers: test-driven-development](https://github.com/obra/superpowers/tree/main/skills/test-driven-development), [wshobson: tdd-cycle.md](https://github.com/wshobson/agents/blob/main/plugins/tdd-workflows/commands/tdd-cycle.md) |
| Skill | `systematic-debugging` | [superpowers: systematic-debugging](https://github.com/obra/superpowers/tree/main/skills/systematic-debugging), [wshobson: debugging-strategies](https://github.com/wshobson/agents/tree/main/plugins/developer-essentials/skills/debugging-strategies) |
| Skill | `verify-before-done` | [superpowers: verification-before-completion](https://github.com/obra/superpowers/tree/main/skills/verification-before-completion), [agency-agents: testing-reality-checker.md](https://github.com/msitarzewski/agency-agents/blob/main/testing/testing-reality-checker.md), own |
| Skill | `review-code` | [wshobson: code-review-excellence](https://github.com/wshobson/agents/tree/main/plugins/developer-essentials/skills/code-review-excellence), [superpowers: requesting-code-review](https://github.com/obra/superpowers/tree/main/skills/requesting-code-review), [agency-agents: engineering-code-reviewer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-code-reviewer.md), own |
| Skill | `receive-review` | [superpowers: receiving-code-review](https://github.com/obra/superpowers/tree/main/skills/receiving-code-review) |
| Skill | `finish-branch` | [superpowers: finishing-a-development-branch](https://github.com/obra/superpowers/tree/main/skills/finishing-a-development-branch), [wshobson: git-workflow.md](https://github.com/wshobson/agents/blob/main/plugins/git-pr-workflows/commands/git-workflow.md) |
| Skill | `onboard-codebase` | [agency-agents: engineering-codebase-onboarding-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-codebase-onboarding-engineer.md), own |
| Skill | `architecture-decision` | [wshobson: architecture-decision-records](https://github.com/wshobson/agents/tree/main/plugins/documentation-generation/skills/architecture-decision-records), [agency-agents: engineering-software-architect.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-software-architect.md) |
| Skill | `threat-model` | [agency-agents: security-architect.md](https://github.com/msitarzewski/agency-agents/blob/main/security/security-architect.md), [wshobson: stride-analysis-patterns](https://github.com/wshobson/agents/tree/main/plugins/security-scanning/skills/stride-analysis-patterns) |
| Skill | `leaked-secret` | [agency-agents: security-secrets-credential-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/security/security-secrets-credential-engineer.md) |
| Skill | `refactor-safely` | [wshobson: refactor-clean.md](https://github.com/wshobson/agents/blob/main/plugins/codebase-cleanup/commands/refactor-clean.md), own |
| Skill | `write-docs` | [agency-agents: engineering-technical-writer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-technical-writer.md), own |
| Skill | `release-notes` | [wshobson: changelog-automation](https://github.com/wshobson/agents/tree/main/plugins/documentation-generation/skills/changelog-automation), own |
| Skill | `write-skill` | [anthropic-skills: skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator), [superpowers: writing-skills](https://github.com/obra/superpowers/tree/main/skills/writing-skills), own |
| Command | `/write-plan` | [superpowers: writing-plans](https://github.com/obra/superpowers/tree/main/skills/writing-plans), own |
| Command | `/review` | [wshobson: full-review.md](https://github.com/wshobson/agents/blob/main/plugins/comprehensive-review/commands/full-review.md), own |
| Command | `/debug` | [wshobson: smart-debug.md](https://github.com/wshobson/agents/blob/main/plugins/debugging-toolkit/commands/smart-debug.md), [superpowers: systematic-debugging](https://github.com/obra/superpowers/tree/main/skills/systematic-debugging) |
| Command | `/tdd` | [wshobson: tdd-cycle.md](https://github.com/wshobson/agents/blob/main/plugins/tdd-workflows/commands/tdd-cycle.md), [superpowers: test-driven-development](https://github.com/obra/superpowers/tree/main/skills/test-driven-development) |
| Command | `/onboard` | [agency-agents: engineering-codebase-onboarding-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-codebase-onboarding-engineer.md), [wshobson: onboard.md](https://github.com/wshobson/agents/blob/main/plugins/git-pr-workflows/commands/onboard.md), own |
| Command | `/adr` | [wshobson: architecture-decision-records](https://github.com/wshobson/agents/tree/main/plugins/documentation-generation/skills/architecture-decision-records) |
| Command | `/finish` | [superpowers: finishing-a-development-branch](https://github.com/obra/superpowers/tree/main/skills/finishing-a-development-branch) |
| Command | `/release-notes` | [wshobson: changelog-automation](https://github.com/wshobson/agents/tree/main/plugins/documentation-generation/skills/changelog-automation), own |
| Agent | `planner` | [agency-agents: project-manager-senior.md](https://github.com/msitarzewski/agency-agents/blob/main/project-management/project-manager-senior.md), [superpowers: writing-plans](https://github.com/obra/superpowers/tree/main/skills/writing-plans) |
| Agent | `reviewer` | [agency-agents: engineering-code-reviewer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-code-reviewer.md), [wshobson: code-reviewer.md](https://github.com/wshobson/agents/blob/main/plugins/comprehensive-review/agents/code-reviewer.md), own |
| Agent | `security-reviewer` | [agency-agents: security-architect.md](https://github.com/msitarzewski/agency-agents/blob/main/security/security-architect.md), [wshobson: security-auditor.md](https://github.com/wshobson/agents/blob/main/plugins/comprehensive-review/agents/security-auditor.md) |
| Agent | `debugger` | [wshobson: debugger.md](https://github.com/wshobson/agents/blob/main/plugins/debugging-toolkit/agents/debugger.md), [superpowers: systematic-debugging](https://github.com/obra/superpowers/tree/main/skills/systematic-debugging) |
| Agent | `tester` | [agency-agents: testing-test-automation-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/testing/testing-test-automation-engineer.md), [superpowers: test-driven-development](https://github.com/obra/superpowers/tree/main/skills/test-driven-development) |
| Agent | `docs-writer` | [agency-agents: engineering-technical-writer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-technical-writer.md) |

### Web (`yaap-web`)

| Kind | Item | Sources |
|---|---|---|
| Standard | `web` | [agency-agents: testing-accessibility-auditor.md](https://github.com/msitarzewski/agency-agents/blob/main/testing/testing-accessibility-auditor.md), [wshobson: typescript-advanced-types](https://github.com/wshobson/agents/tree/main/plugins/javascript-typescript/skills/typescript-advanced-types), own |
| Skill | `frontend-design` | [anthropic-skills: frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design) |
| Skill | `react-state` | [wshobson: react-state-management](https://github.com/wshobson/agents/tree/main/plugins/frontend-mobile-development/skills/react-state-management) |
| Skill | `typescript-types` | [wshobson: typescript-advanced-types](https://github.com/wshobson/agents/tree/main/plugins/javascript-typescript/skills/typescript-advanced-types) |
| Skill | `e2e-testing` | [wshobson: e2e-testing-patterns](https://github.com/wshobson/agents/tree/main/plugins/developer-essentials/skills/e2e-testing-patterns), [agency-agents: testing-test-automation-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/testing/testing-test-automation-engineer.md), [anthropic-skills: webapp-testing](https://github.com/anthropics/skills/tree/main/skills/webapp-testing), own |
| Skill | `accessibility-audit` | [agency-agents: testing-accessibility-auditor.md](https://github.com/msitarzewski/agency-agents/blob/main/testing/testing-accessibility-auditor.md) |
| Skill | `ui-finish-gate` | [agency-agents: design-ui-finish-gate-reviewer.md](https://github.com/msitarzewski/agency-agents/blob/main/design/design-ui-finish-gate-reviewer.md) |
| Agent | `frontend-reviewer` | [agency-agents: design-ui-finish-gate-reviewer.md](https://github.com/msitarzewski/agency-agents/blob/main/design/design-ui-finish-gate-reviewer.md), own |

### Python (`yaap-python`)

| Kind | Item | Sources |
|---|---|---|
| Standard | `python` | [wshobson: python-type-safety](https://github.com/wshobson/agents/tree/main/plugins/python-development/skills/python-type-safety), [wshobson: uv-package-manager](https://github.com/wshobson/agents/tree/main/plugins/python-development/skills/uv-package-manager), own |
| Skill | `python-testing` | [wshobson: python-testing-patterns](https://github.com/wshobson/agents/tree/main/plugins/python-development/skills/python-testing-patterns) |
| Skill | `api-design` | [wshobson: api-design-principles](https://github.com/wshobson/agents/tree/main/plugins/backend-development/skills/api-design-principles), [agency-agents: engineering-api-platform-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-api-platform-engineer.md), [agency-agents: engineering-backend-architect.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-backend-architect.md) |
| Skill | `fastapi-service` | [wshobson: fastapi-templates](https://github.com/wshobson/agents/tree/main/plugins/api-scaffolding/skills/fastapi-templates), own |
| Skill | `postgres-schema` | [wshobson: postgresql-table-design](https://github.com/wshobson/agents/tree/main/plugins/database-design/skills/postgresql-table-design) |
| Skill | `safe-migrations` | [agency-agents: engineering-database-reliability-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-database-reliability-engineer.md), [wshobson: sql-migrations.md](https://github.com/wshobson/agents/blob/main/plugins/database-migrations/commands/sql-migrations.md) |
| Skill | `build-mcp-server` | [anthropic-skills: mcp-builder](https://github.com/anthropics/skills/tree/main/skills/mcp-builder), [agency-agents: specialized-mcp-builder.md](https://github.com/msitarzewski/agency-agents/blob/main/specialized/specialized-mcp-builder.md) |

### Data (`yaap-data`)

| Kind | Item | Sources |
|---|---|---|
| Standard | `data` | [agency-agents: engineering-data-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-data-engineer.md), [wshobson: data-quality-frameworks](https://github.com/wshobson/agents/tree/main/plugins/data-engineering/skills/data-quality-frameworks), own |
| Skill | `sql-style-and-tuning` | [wshobson: sql-optimization-patterns](https://github.com/wshobson/agents/tree/main/plugins/developer-essentials/skills/sql-optimization-patterns), [agency-agents: engineering-database-optimizer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-database-optimizer.md) |
| Skill | `pipeline-design` | [agency-agents: engineering-data-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-data-engineer.md) |
| Skill | `dbt-models` | [wshobson: dbt-transformation-patterns](https://github.com/wshobson/agents/tree/main/plugins/data-engineering/skills/dbt-transformation-patterns) |
| Skill | `airflow-dags` | [wshobson: airflow-dag-patterns](https://github.com/wshobson/agents/tree/main/plugins/data-engineering/skills/airflow-dag-patterns) |
| Skill | `data-quality` | [wshobson: data-quality-frameworks](https://github.com/wshobson/agents/tree/main/plugins/data-engineering/skills/data-quality-frameworks) |
| Agent | `data-engineer` | [agency-agents: engineering-data-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-data-engineer.md) |

### DevOps (`yaap-devops`)

| Kind | Item | Sources |
|---|---|---|
| Standard | `operations` | [wshobson: github-actions-templates](https://github.com/wshobson/agents/tree/main/plugins/cicd-automation/skills/github-actions-templates), [wshobson: secrets-management](https://github.com/wshobson/agents/tree/main/plugins/cicd-automation/skills/secrets-management), own |
| Skill | `docker-images` | own |
| Skill | `github-actions` | [wshobson: github-actions-templates](https://github.com/wshobson/agents/tree/main/plugins/cicd-automation/skills/github-actions-templates), own |
| Skill | `incident-response` | [agency-agents: engineering-incident-response-commander.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-incident-response-commander.md) |
| Skill | `slos` | [agency-agents: engineering-sre.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-sre.md) |
| Skill | `kubernetes-manifests` | [wshobson: k8s-manifest-generator](https://github.com/wshobson/agents/tree/main/plugins/kubernetes-operations/skills/k8s-manifest-generator), [wshobson: k8s-security-policies](https://github.com/wshobson/agents/tree/main/plugins/kubernetes-operations/skills/k8s-security-policies) |
| Agent | `devops` | [agency-agents: engineering-devops-automator.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-devops-automator.md), [wshobson: deployment-engineer.md](https://github.com/wshobson/agents/blob/main/plugins/cicd-automation/agents/deployment-engineer.md) |

### Writing (`yaap-writing`)

| Kind | Item | Sources |
|---|---|---|
| Standard | `writing` | own |
| Skill | `co-write-document` | own |
| Skill | `status-update` | [anthropic-skills: internal-comms](https://github.com/anthropics/skills/tree/main/skills/internal-comms) |
| Skill | `check-before-acting` | [anthropic-skills: discernment-nudge](https://github.com/anthropics/skills/tree/main/skills/discernment-nudge) |
| Skill | `product-requirements` | [agency-agents: product-manager.md](https://github.com/msitarzewski/agency-agents/blob/main/product/product-manager.md) |
| Skill | `prioritise` | [agency-agents: product-sprint-prioritizer.md](https://github.com/msitarzewski/agency-agents/blob/main/product/product-sprint-prioritizer.md) |
| Skill | `prompt-design` | [agency-agents: engineering-prompt-engineer.md](https://github.com/msitarzewski/agency-agents/blob/main/engineering/engineering-prompt-engineer.md) |
| Skill | `research-and-summarise` | own |
| Agent | `researcher` | own |

### Built-in

YAAP's own built-in items are written for YAAP. The one you work with directly:

| Kind | Item | Sources |
|---|---|---|
| Skill | `specification` | own |
