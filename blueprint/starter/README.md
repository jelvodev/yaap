# YAAP's starter

A ready set of Blueprint items for a new YAAP installation: standards, skills, commands and agents that make the
agent work in a careful, test-first way from the first conversation. It is opinionated on purpose: every rule says
why, so you can see what to change when your own way differs.

YAAP offers it on its first start and under Blueprint › *Add from a package* › *YAAP starter*. You can also install
any pack from its folder link like other [Blueprint packages](../../docs/blueprint-packages.md).

## The packs

| Folder | Package | For |
|---|---|---|
| [core](core) | `yaap-core` | Everyone: how to work, test, debug, review and finish a change. Install this one first. |
| [web](web) | `yaap-web` | Web front ends in TypeScript and React. |
| [python](python) | `yaap-python` | Python back ends and APIs. |
| [data](data) | `yaap-data` | Data engineering: SQL, pipelines, dbt and Airflow. |
| [devops](devops) | `yaap-devops` | Containers, CI, infrastructure and operations. |
| [writing](writing) | `yaap-writing` | Documents, status updates, product work and research. |

## How it is meant to be used

- **Standards** go with every reply, so they are few and short. The full text behind them loads when the agent needs
  it.
- **Skills** load only when a task calls for them; that is where most of the knowledge lives.
- **Commands** start a skill or an agent: `/write-plan`, `/review`, `/debug`, `/tdd`, `/onboard`, `/adr`, `/finish`
  and `/release-notes` in the core.
- **Agents** pick their model by preset (`Deep`, `Cheap`) or as the worker model. YAAP creates these presets in every
  environment; when you rename or remove one, a delegated agent that names it uses the worker model of the
  conversation's preset. Agents that only
  read list only reading tools, so they cannot change files.

A good first step in a project: type `/onboard`. The agent maps the project and proposes what belongs in its memory.

## Versions and updates

The starter is pinned to the tag of your YAAP version. After you update YAAP, the starter panel offers the items that
changed as a diff; you accept them one by one, and items you changed yourself show your change beside the new one.

## Credits

The starter is written new for YAAP. Its methods and checklists draw on the work of others, credited per item in
[docs/credits.md](../../docs/credits.md) and in each pack's `NOTICE.md`.
