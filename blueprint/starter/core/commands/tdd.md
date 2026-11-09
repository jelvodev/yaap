---
sources: [wshobson/plugins/tdd-workflows/commands/tdd-cycle.md, superpowers/skills/test-driven-development]
description: Build one behaviour test-first, with a failing test, the least code that passes it, then a clean-up.
---

Use the `test-driven-development` skill (load it with `load_skill`) for one red, green, refactor cycle for this
behaviour:

$ARGUMENTS

Write one test, run it and show that it fails for the expected reason. Then write the least code that makes it pass
and run it together with the tests around it. Clean up only while everything stays green. When the behaviour needs
more than one test, list the next ones and do only the first.

End with the test's name, the command, its output when red and when green, and the next test to write.
