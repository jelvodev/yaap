---
name: co-write-document
description: Write a substantial document with the user (a proposal, design, report, guide, policy or long email) by
  gathering context, agreeing on an outline, drafting section by section and testing it on a fresh reader. Use it
  whenever the user asks to draft or rework a document others will read and act on.
license: MIT
metadata:
  sources: own
---

# Co-write a document

A good document comes from knowing its reader and its purpose, then building it in parts the user can steer. Writing
it all at once and asking "is this good?" leaves the user to find every gap alone.

## 1. Gather the context

Before writing anything, find out:

- **Purpose.** What should the reader know, decide or do after reading it?
- **Reader.** Who reads it, what do they already know, and how much time will they give it?
- **Shape.** Is there a template, a length, a format, a deadline? Is it a new document or a rework of one?
- **Material.** What exists already: notes, earlier versions, attachments, files in the project, knowledge sources,
  conversations. Ask the user to share it, unsorted is fine.

Ask the few questions that matter, a couple at a time, not a long form. Read the material the user gives you
(`read_attachment`, `read_file`, `read_knowledge`) before asking what it already answers. Note open questions and
move on when you have enough to outline; the rest can be asked per section.

## 2. Agree on an outline

Propose the headings, each with one line on what it holds and why the reader needs it. Put the point of the whole
document in the first section. Keep the outline short enough to change easily; changing an outline costs little,
rewriting finished sections does not.

Write it to a Markdown file in the project. When `open_document` is available (in a project conversation), open the
file with it, so the user can read and edit it beside the chat; their saved edits reach you, so build on them rather
than on your own earlier text. Otherwise keep the draft in the reply and show each changed section again after a
round of edits.

## 3. Write section by section

Start with the section that carries the most uncertainty or the core argument, not necessarily the first. For each
section:

1. Ask what must go in, if the material does not settle it. Offer a short list of candidate points and let the user
   keep, drop or add.
2. Draft the section in the document (or in the reply).
3. Ask for changes. Apply them precisely; when the user rewrites a passage, keep their wording and learn their style
   from it for the next sections.
4. Move on when the user is satisfied. Do not polish one section while the others are empty.

When the user's edits show a preference (shorter, more formal, no bullet lists), apply it to the whole document and
say so.

## 4. Test on a fresh reader

You and the user know too much to read it as a newcomer. When `delegate` is available (in a project conversation),
use an exploring worker: it does not see this conversation, so it reads the document as the intended reader would.
Otherwise suggest that the user gives it to a colleague who has not seen it. Give the reader the file, a one-line
description of who they stand in for, and ask:

- What is this document asking of you, in one sentence?
- Which part was unclear, and where did you have to guess?
- What question would you still ask the author?

Compare its answers with the purpose from step 1. Where they differ, the document is unclear, not the reader. Fix
those places and, for a large change, test once more.

## 5. Final pass

- The point is in the first lines; each section starts with its own point.
- Cut what this reader does not need; link to detail instead of including it.
- Terms, names and numbers are consistent throughout; every fact that needs one has its source.
- Every request has an owner and, where it matters, a date.

Follow the `writing` standard when the Blueprint has it (`load_standard`).

## Report

End with this shape:

```
Document: <path>
Reader test: <what the fresh reader got right, and what was unclear>
Changed after the test: <the fixes, or "nothing">
Open: <questions the user still has to answer, or "nothing">
```
