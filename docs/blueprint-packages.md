# Blueprint packages: `blueprint-package.json`

A Blueprint package is a GitHub repository, or a folder in one, that offers Blueprint items for others to install:
standards, engineering standards, skills, commands and agents. YAAP reads it from a link (*Add from a package* in the
Blueprint), shows every item for review, pins what it installs to a commit and updates it only through a diff the user
accepts. There is no central list in YAAP; a list of known packages can be a README anywhere.

## The manifest

The package folder holds a file `blueprint-package.json`:

```json
{
  "name": "acme-basics",
  "description": "General engineering standards, a review skill and a release command.",
  "items": [
    { "kind": "standard", "path": "standards/security.md" },
    { "kind": "engineering", "path": "engineering/testing.md", "description": "How we test." },
    { "kind": "skill", "path": "skills/review-code" },
    { "kind": "command", "path": "commands/ship.md" },
    { "kind": "agent", "path": "agents/helper.md", "name": "acme-helper" }
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | The package's name: lowercase letters, digits and hyphens, at most 60. It names the record in each layer it is installed in. |
| `description` | yes | One or two sentences shown in the review. May be empty. |
| `items` | yes | 1 to 60 items. |
| `items[].kind` | yes | `standard`, `engineering`, `skill`, `command` or `agent`. `instructions.md` is personal and cannot come from a package. |
| `items[].path` | yes | Relative to the package folder: a Markdown file (`.md`) for a standard, engineering standard, command or agent; a folder with a `SKILL.md` for a skill (a path to the `SKILL.md` itself is accepted too). It may not leave the package folder. |
| `items[].name` | no | The name in the Blueprint, in the same form as `name`. Without it: the file name without `.md`, or the skill's folder name. The user can still rename an item before installing. |
| `items[].description` | no | Shown in the review. Without it: `summary` or `description` from the item's front matter. |

Other top-level fields are ignored, so a package may add its own (for example a homepage). Two items may not end up at
the same Blueprint path.

An item is written to the layer as it is in the package, at the path its kind and name give:
`standards/<name>.md`, `engineering/<name>.md`, `commands/<name>.md`, `agents/<name>.md` or `skills/<name>/` (the
whole folder). Only the line endings of a single-file item (every kind but a skill) change: they become LF. The files use the
normal Blueprint format, with front matter where the kind expects it.

## Text only

A package is text, never code. YAAP refuses an item when it contains any of the following. A script or binary suffix, the executable bit and a zero byte in the item's main file show in the review, where the item cannot be chosen; the other cases (a `#!` first line, a zero byte in another file, text that is not UTF-8) are found when installing, which then fails with the reason:

- a script, by suffix (`.sh`, `.ps1`, `.py`, `.js`, … as for skills from GitHub), by the executable bit, or by a
  `#!` first line;
- a binary file, by suffix (`.exe`, `.dll`, `.zip`, …) or because it contains a zero byte;
- a file that is not UTF-8 text.

Symlinks and submodules are skipped, and so is a `source.json` in a skill's folder (YAAP writes its own). Limits per
item: 512 KB for a single file, 5 MB and 200 files for a skill. The manifest itself is at most 64 KB. An item whose
front matter is not valid YAML is refused too; for a skill it is only a warning in the review.

The review also warns when an item's text tells the agent to download or install things (such as `curl … | sh`,
`pip install`, `npm install` or `sudo`). The item can still be chosen; read it first.

## YAAP's starter

YAAP's own starter is a set of six packages in this repository, under [`blueprint/starter/`](../blueprint/starter): `yaap-core`, `yaap-web`, `yaap-python`, `yaap-data`, `yaap-devops` and `yaap-writing`. The **YAAP starter** button under *Add from a package* (and the first start) lists them without a link to paste:

- **Pinned to the version.** YAAP reads them at the tag of its own version (`v0.5.7` for 0.5.7). A build without such a tag reads `main`, and the panel says so.
- **After an update of YAAP** an installed pack whose tag is older shows *Update*: its diff against the new tag, item by item, as *Check for updates* shows it for any package. Accepting records the new tag.
- **Installed** means the level has the package's record with at least one of its items.

Read as packages, they follow every rule on this page: text only, a review per item, nothing written until you install.

## Links

- the repository: `https://github.com/<owner>/<repo>` (the manifest is in the root, the default branch is used);
- a folder: `https://github.com/<owner>/<repo>/tree/<ref>/<path>`;
- the manifest: `https://github.com/<owner>/<repo>/blob/<ref>/<path>/blueprint-package.json`.

Private repositories use the GitHub token of the environment, as skills from GitHub do.

## What YAAP records

Installing writes `packages/<name>.json` in the chosen layer, next to the items and committed with them:

```json
{
  "name": "acme-basics",
  "description": "General engineering standards, a review skill and a release command.",
  "url": "https://github.com/acme/blueprints",
  "repository": "acme/blueprints",
  "ref": "main",
  "path": "",
  "items": {
    "standards/security.md": {
      "kind": "standard",
      "name": "security",
      "source": "standards/security.md",
      "commit": "3f5c…",
      "installed_at": "2026-11-07T10:00:00+00:00",
      "hash": "…"
    }
  }
}
```

Each item keeps its own commit, because an update can be accepted for some items and not for others. The hash shows
when an item was changed in YAAP after it was installed ("changed locally"). A skill also gets the usual
`skills/<name>/source.json`, with `"package": "<name>"`.
