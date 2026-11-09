---
sources: [agency-agents/engineering/engineering-codebase-onboarding-engineer.md, wshobson/plugins/git-pr-workflows/commands/onboard.md, own]
description: Map this project from its code and propose what belongs in its project memory.
---

Use the `onboard-codebase` skill (load it with `load_skill`) to map this project. Focus, if any (otherwise the whole
project):

$ARGUMENTS

State only what you read, with the file for each claim, and say which parts you did not read. When `delegate` is
available, hand wide searches to an exploring worker (mode explore). Change no files.

End with the map in the skill's format and a short numbered list of proposed project memory entries, each with its
section (Decisions, Conventions, Lessons learned or Notes) and leaving out what the memory already holds. Ask which
ones to keep. Add only the entries the user agrees to, one `update_memory` call per entry.
