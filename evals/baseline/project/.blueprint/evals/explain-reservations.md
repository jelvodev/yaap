---
summary: "Baseline: read the large inventory module and explain how reservations expire"
start:
  commit: 8916086225c95afd2d35e7e00a5614ee4099cc15
allowed_tools: [list_files, read_file, search, run_command]
budget:
  steps: 30 steps
  tokens: 600k tokens
  time: 10 min
checks:
  - kind: command
    name: nothing changed
    run: git diff --exit-code HEAD
    timeout: 1 min
  - kind: rubric
    name: names the mechanism
    rubric: >-
      The reply explains that a reservation expires when the clock reaches its expires_at
      (Reservation.is_expired), that Inventory.expire_reservations releases every expired reservation through
      release, which lowers the item's reserved count so the units become available again while the quantity on
      the shelf stays the same and no movement is recorded. It names at least two places that call
      expire_reservations first (reserve, reserve_kit, OrderBook.pick, the CLI's main) and says that an expired
      reservation can no longer be fulfilled. It changes no files.
---

How does a reservation in this project expire, and what happens to the stock when it does? Answer with the functions
involved and where they are called from. Do not change anything.
