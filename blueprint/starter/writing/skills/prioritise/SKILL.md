---
name: prioritise
description: Rank a backlog, feature ideas, requests or a release scope with RICE scores or MoSCoW, and make the
  trade-offs visible. Use it whenever the user asks what to do first, what to cut, what fits in a sprint or release,
  or how to say no with reasons, even when they only paste a list.
license: MIT
metadata:
  sources: agency-agents/product/product-sprint-prioritizer.md
---

# Prioritise

Prioritising is choosing what not to do now. A method does not make the choice for you; it makes the reasons
visible, so people can disagree with a number instead of with each other.

## 1. Pick the method

- **RICE** to rank many candidates against each other, when you can estimate their reach and effort. Good for a
  backlog or a roadmap.
- **MoSCoW** to cut a fixed scope down to what fits: one release, one sprint, one deadline. Good when the question is
  "what is in and what is out".

Both can be combined: MoSCoW for the release, RICE to order the "should" items.

Before scoring, agree on the goal the list serves (more active users, fewer support tickets, a contract deadline).
Without one, every score is an opinion.

## 2. RICE

For each item estimate:

| Factor | Meaning | Scale |
|---|---|---|
| Reach | How many people or events it affects in a fixed period | a count, for example users per quarter |
| Impact | How much it moves the goal for each of them | 3 large, 2 high, 1 medium, 0.5 low, 0.25 minimal |
| Confidence | How sure you are of the other estimates | 100% data, 80% some evidence, 50% a guess |
| Effort | Work to deliver it | person-months (or person-weeks, the same unit for all) |

Score = Reach x Impact x Confidence / Effort.

Example, reach per quarter:

| Item | Reach | Impact | Confidence | Effort | Score |
|---|---|---|---|---|---|
| Saved filters | 1200 | 0.5 | 100% | 1 | 600 |
| Bulk export | 400 | 1 | 80% | 2 | 160 |
| Single sign-on | 150 | 3 | 50% | 3 | 75 |

Then:

- Write down where each estimate came from. A reach number from usage data and one from a hunch are not equal; the
  confidence factor says so.
- Sort by score, then look at the list as a whole. Override a score when there is a reason the formula cannot see
  (a contract, a legal duty, a dependency that unlocks other items) and write the reason next to it.
- Check how sensitive the top of the list is: if halving one doubtful estimate reorders it, say that the order rests
  on that estimate and suggest how to firm it up.

## 3. MoSCoW

Sort each item into one of four groups, against the goal and the deadline:

- **Must:** without it the release fails its goal or breaks a commitment. Test: would you delay the release for it?
  If not, it is not a must.
- **Should:** important, but the release still works without it, perhaps with a workaround.
- **Could:** nice to have; the first to go when time runs short.
- **Won't (this time):** agreed to be out of this release. Naming these prevents them from creeping back in.

Keep the musts to about 60% of the available effort, so the shoulds and coulds are the buffer when estimates turn out
low. When the musts alone exceed the capacity, the scope or the deadline has to change; say that plainly instead of
squeezing.

## 4. Capacity and dependencies

- Estimate capacity from what the team actually delivered in recent periods, minus holidays and known other work,
  not from an ideal week.
- Mark items that depend on another item or on another team; a high score is worth little when its dependency is
  months away.

## 5. Saying no

Every "no" or "not now" gets a reason the requester can follow: the goal it serves less, what it would push out, and
when it will be looked at again. Record the decision, so the same request is not argued from scratch next month.

## Output

```
Goal: <what the ranking serves>
Method: <RICE | MoSCoW | both>

<the table, sorted, with sources for the estimates>

Overrides: <item, reason; or "none">
Sensitive to: <the estimate the order depends on most>
Out, and why: <items not chosen, with the reason for each>
```

Keep the result in a file in the project when it will be revisited, so the next round starts from it.
