---
summary: "Baseline: a plain question that needs no tools"
start:
  commit: 8916086225c95afd2d35e7e00a5614ee4099cc15
budget:
  steps: 3 steps
  tokens: 80k tokens
  time: 2 min
checks:
  - kind: command
    name: nothing changed
    run: git diff --exit-code HEAD
    timeout: 1 min
  - kind: rubric
    name: correct and short
    rubric: >-
      The reply explains in a few sentences that FIFO (first in, first out) ships the oldest received stock first,
      and FEFO (first expired, first out) ships the stock with the earliest expiry date first, which matters for
      perishable goods such as food. It is short and does not change files.
---

In two or three sentences: what is the difference between FIFO and FEFO stock rotation?
