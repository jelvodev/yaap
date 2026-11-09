---
sources: [wshobson/plugins/documentation-generation/skills/changelog-automation, own]
description: Write release notes and a changelog entry from the commits since the last tag.
---

Use the `release-notes` skill (load it with `load_skill`). The version or range, if given (otherwise everything since
the latest tag):

$ARGUMENTS

Read the commits in that range and, where a subject is unclear, the change itself. Write for the people who use the
project, not for its developers: what is new, what changed, what was fixed, and first of all what they must do
(breaking changes, migrations, new settings).

End with the draft. Change the changelog file only when the user agrees, and never create a tag or a release.
