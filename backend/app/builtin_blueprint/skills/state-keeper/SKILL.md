---
description: Tells the utility model how to rewrite a project's Current state, the short block every new conversation starts from.
tasks:
  - current_state
when: After a goal or a build ends, and after a long conversation has been quiet for a while, when the agent did not update the block itself.
---
## Refresh

You keep the Current state of a software project: a short block at the top of its memory that says where the work
stands, so a new conversation can pick up where the last one stopped. Below are the block as it is now and what
happened since (a conversation, a goal or a build).

Rewrite the block. Use exactly three bold lead-ins, each followed by one to three short sentences or a short list:

**Where it stands:** what works and what was finished most recently.
**Open:** what is unfinished, broken or waiting for a decision.
**Next step:** the one thing to do next.

Rules:
- Keep what is still true from the current block; drop what is done or no longer relevant.
- Facts only from the material below; invent nothing. Leave out the steps the agent took and temporary details.
- At most {max_chars} characters, in {language}. No headings, no introduction: only the block's text.
