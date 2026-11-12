---
description: Tells the utility model how to title a conversation and how to suggest and tidy project memory entries.
tasks:
  - title
  - memory_suggestion
  - memory_tidy
when: After the first reply of a conversation (title), after a goal or every few turns (suggestions), and when the memory grows (tidy).
---
## Suggestions

You keep the memory of a software project: short entries that later conversations about this project
should know. Read the conversation below and propose at most {limit} new entries.

Good entries are lasting: decisions and why, conventions of the codebase, lessons learned from mistakes, facts about
the project's setup. Leave out what only mattered for this conversation: the task's steps, temporary files, ports,
what the agent tried, and anything the memory already says.

## Tidy

You keep the memory of a software project tidy. Below are its entries, numbered, each with its section.
Find entries that say the same thing, or overlap so much that one entry would serve better, and propose merges.

Rules:
- A merge combines two or more entries into one entry that keeps every fact, decision and reason of the entries it
  replaces. Leave nothing out and add nothing new.
- Only merge entries about the same subject; entries that merely share a topic stay apart.
- Each entry appears in at most one merge. Propose at most {limit} merges, the clearest first.
- Write each merged entry in {language}, as one to three sentences of at most {max_chars} characters.

## Title

a title of at most {words} words for this conversation, in {language}, with no punctuation at the end
