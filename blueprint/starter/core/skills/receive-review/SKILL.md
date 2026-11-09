---
name: receive-review
description: Handle review feedback by checking each point against the code before acting, then fixing it, pushing
  back with evidence or asking. Use it whenever review comments arrive (from the user, a pull request, the reviewer
  agent or Review these changes), above all when a point seems wrong.
license: MIT
metadata:
  sources: superpowers/skills/receiving-code-review
---

# Receive a review

Check before you change. A reviewer saw part of the picture: the diff, not always the reasons, the callers or the
user's earlier decisions. Your job is to find out which points are right for this code, fix those well, and say
plainly why the others stand.

## 1. Read all of it first

Read every point before changing anything; points are often related, and a fix for one can settle or break another.
Restate each point in a line of your own. If you cannot, the point is unclear.

**Unclear points stop the work.** Ask about them before implementing any of the others: "I follow 1, 2 and 4; for 3,
do you mean the retry in the client or in the queue?" A partial understanding leads to a wrong fix.

## 2. Check each point against the code

For every point, find out:

- **Is it true here?** Read the code it names and around it. Follow the callers with `find_references`. Reproduce
  it with a test when it claims a bug.
- **Would the fix break something?** Run the tests around it; look for other users of the code.
- **Is there a reason for the current code?** A comment, a commit message (`git log` on the lines), the project
  memory, an earlier decision of the user, a platform or compatibility limit.
- **Is it needed?** A suggestion to "do it properly" with more features: search whether anything uses it. Unused
  code is better removed than extended.

## 3. Decide per point

- **Agree:** the point holds. Fix it.
- **Push back:** the point does not hold for this code. Say why with evidence: the test that covers it, the caller
  that guarantees it, the constraint that requires it. Technical reasons, no defensiveness.
- **Ask:** you cannot settle it without information you do not have, or it touches an earlier decision of the user
  or the architecture. Say what you would need and what you would choose.

Who it comes from changes how much you check, not whether:

- **The user:** trusted on what they want. Still check that the fix is right for the code, and ask when the scope is
  unclear.
- **Another reviewer** (the `reviewer` agent, a pull request comment, *Review these changes* by a model of another
  vendor): check carefully. A model of another vendor sees only the diff and the standards, not the conversation.
- **When a point conflicts with what the user decided earlier,** do not choose yourself. Put both to the user.

## 4. Fix one point at a time

Order: blocking points first (wrong results, security, data loss), then the simple ones, then the larger ones.
For each: write a test that fails because of the problem, fix it, run the test and the suite. Then the next point.
One fix per change keeps it easy to see which fix broke what.

## 5. Answer briefly

- **Fixed:** what changed and where. "Fixed: the date is now parsed as UTC (`dates.py:31`), with a test for 23:30."
- **Not changed:** the reason and the evidence. "Kept: `load()` is only called after `validate()`, see `api.py:88`;
  the test `test_load_rejects_unvalidated` covers it."
- **You were wrong to push back:** say so in one line and fix it. "Checked again, you are right: the cache is shared
  between requests. Fixing."

Skip the thanks and the praise of the feedback ("great catch!"); the fix shows you heard it. Do not agree with a
point you have not checked.

## Output template

```
| # | Point | Verdict | Reason or evidence | Action |
|---|---|---|---|---|
| 1 | Empty list crashes `total()` | agree | reproduced with a test | fixed in `cart.py:14` |
| 2 | Use a class instead of a dict | push back | the dict is the API's JSON shape, see `api.py:40` | none |
| 3 | Retry on timeout | ask | which layer should retry? | waiting |

Tests: <command> -> <result>
```
