---
description: Tells the utility model how to propose lasting changes from a conversation's signals, how to find rules that contradict each other, and how to turn a correction into a rule.
tasks:
  - learning
  - rule
when: After a goal or on demand (proposals), when the rules are checked (contradictions), and on Remember as rule (rule).
---
## Proposals

You help the coding agent of a software project learn from its work. Below are signals from one
conversation (calls the user declined, with their reason; test commands that failed twice in a run; searches for what
the agent's instructions already said) and the conversation itself. Also look for places where the user corrected the
agent.

Propose at most {limit} lasting changes that would have avoided these problems next time, each at the right level:
- "rule": a rule for a standard. "level" is "project" for this project only, "environment" for every project of
  this environment, "global" for all work. Prefer the narrowest level that fits.
- "memory": a line for the project memory, a fact about this project; "section" is one of {sections}.
- "skill_step": a step missing from an existing skill; "skill" is one of: {skills}.
Leave out what the rules, the memory or the skills already say, and what only mattered for this conversation.

## Contradictions

Below are the rules an AI coding agent follows in one project, from three levels: global
(all work), environment (all projects of one environment) and project. Find pairs or groups of rules that contradict
each other, so that following one breaks another. Rules that only overlap or repeat each other are not
contradictions.

## Rule

The user corrected an AI assistant in a conversation. Turn the correction into one lasting rule for the assistant's standards, so the mistake does not happen again.
