---
summary: "Baseline: a TypeScript fix in the web shop with a test"
start:
  commit: 8916086225c95afd2d35e7e00a5614ee4099cc15
allowed_tools: [list_files, read_file, search, edit_file, write_file, run_command]
budget:
  steps: 30 steps
  tokens: 800k tokens
  time: 10 min
checks:
  # The runner's Node.js may be older than 22.18 and unable to run TypeScript, so this check strips the type
  # annotations of format.ts itself and runs formatMoney with plain Node.js.
  - kind: command
    name: negative amounts have the sign first
    run: >-
      node -e "const fs = require('fs');
      const source = fs.readFileSync('web/src/format.ts', 'utf8').replace(/:\s*(number|string|Date|boolean)(\[\])?/g, '').replace(/^export /gm, '');
      const formatMoney = new Function(source + ';return formatMoney;')();
      const want = {'-500': '-€5.00', '-50': '-€0.50', '-123456': '-€1,234.56', '0': '€0.00', '123456': '€1,234.56'};
      for (const cents in want) { if (formatMoney(Number(cents)) !== want[cents]) { console.error(cents, formatMoney(Number(cents))); process.exit(1); } }"
    timeout: 1 min
  - kind: file
    name: a test covers negative amounts
    path: web/test/format.test.ts
    contains: ["-€5.00", "-€0.50"]
  - kind: command
    name: the Python tests still pass
    run: python3 -m unittest discover -s tests -t .
    timeout: 5 min
---

In the web shop, refund lines show negative amounts as `€-5.00`, and amounts below one euro lose their sign: -50
cents shows as `€0.50`. They must read `-€5.00` and `-€0.50`. Fix `formatMoney` in `web/src/format.ts` and add a
test in `web/test/format.test.ts`.
