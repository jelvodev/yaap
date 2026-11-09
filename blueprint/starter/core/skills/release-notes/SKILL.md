---
name: release-notes
description: Write release notes and a changelog entry from the commits since the last tag, grouped by what changed
  for users, with an Action needed part for breaking changes. Use it when preparing a release or a tag, or when asked
  what changed since the last version.
license: MIT
metadata:
  sources: wshobson/plugins/documentation-generation/skills/changelog-automation, own
---

# Release notes

Release notes are for the people who use the software, not for the people who wrote it. A list of commit subjects
answers "what did we do"; the reader asks "what changes for me, and do I have to do anything". Answer that, and put
the part where they must act where nobody can miss it.

## 1. Find the range

- The last release: `git describe --tags --abbrev=0`, or the newest version tag in `git tag --sort=-v:refname`. If
  the project has no tags, ask the user where the last release was.
- The commits since: `git log <tag>..HEAD --no-merges --format="%h %s"`. Read the bodies too
  (`--format="%h %s%n%b"`); a breaking change is often explained only there.
- For a commit whose subject does not say what it does for the user, read its diff.

If the project has a `CHANGELOG.md` with an *Unreleased* section, start from it and check it against the commits;
entries get forgotten.

## 2. Sort each change

Decide per commit what a user notices, and leave out what they do not (refactors, test changes, CI tweaks, typo
fixes in comments). Group what is left, using the project's existing headings if its changelog has them, otherwise:

- **Added:** new features.
- **Changed:** changes to existing behaviour.
- **Fixed:** bugs that are gone, named by their symptom ("Saving a draft no longer loses attachments"), not their
  cause.
- **Removed:** features or options that are gone.
- **Security:** fixed vulnerabilities, with their severity and identifier when they have one.

Several commits for one feature become one entry. A commit that adds a feature and a later one that fixes it before
the release become one entry with no fix.

## 3. Find what the reader must do

Look for: removed or renamed options, settings, commands, endpoints or fields; changed defaults; a changed data
format or migration; new required configuration or environment variables; a raised minimum version of a runtime or
a dependency; anything a commit marks as breaking (`!` after the type, or a `BREAKING CHANGE:` footer). Each gets an
entry under **Action needed** that says what to do, step by step, and what happens if they do not.

When nothing needs action, say "None" rather than leaving the heading out, so the reader knows it was checked.

## 4. Pick the version

With semantic versioning: an *Action needed* entry means a major version (or a minor one before 1.0), new features
a minor one, only fixes a patch. Propose the number and say why; the user decides.

## 5. Write

- Start with two or three sentences that say what the release is about, in plain words.
- Each entry is one line, in the reader's words, starting with what changed: "Exports include the time zone." Not
  "Refactored export service".
- Link the issue or pull request when the project does that.
- Keep the changelog's existing format, order (newest first) and date style.

Show the draft in the reply first. Write it into `CHANGELOG.md` (moving *Unreleased* to the new version and date)
when the user agrees. Do not tag, push or publish: the user does that, and YAAP asks before any push anyway.

## Template

```markdown
## [<version>] - <YYYY-MM-DD>

<Two or three sentences: what this release is about.>

### Action needed
- <What to do, step by step, and what happens if you do not.> (or: None.)

### Added
- <...>

### Changed
- <...>

### Fixed
- <...>

### Removed
- <...>

### Security
- <...>
```

Leave out empty groups other than *Action needed*.
