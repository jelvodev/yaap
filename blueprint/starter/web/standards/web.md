---
sources: [agency-agents/testing/testing-accessibility-auditor.md, wshobson/plugins/javascript-typescript/skills/typescript-advanced-types, own]
category: web
summary: Web front ends are accessible by default, strictly typed, checked in the running app, and only optimised
  after measuring.
rules:
- Meet WCAG 2.2 AA from the start with semantic HTML, keyboard access and visible focus; retrofitting it costs a
  redesign.
- Keep TypeScript strict, with no `any`, `@ts-ignore` or `!` on data that can be absent; they hide the bugs types catch.
- Check data from the network at the boundary with a schema or typed client; the server can change without the compiler.
- Look at every UI change in the running app at desktop and phone width; layout bugs only show when rendered.
- Measure before optimising (bundle size, Web Vitals, the profiler); guessed optimisations add code and miss the cause.
---

# Web

How to build web front ends in TypeScript and React. The rules above are always in the prompt; this text explains
them.

## Accessible by default

WCAG 2.2 level AA is the floor, not a later project. Most of it comes free with the right elements: a `button` for an
action, an `a` for navigation, a `label` for every input, headings in order, one `main`. Reach for ARIA only when no
element does the job, and then follow the matching pattern exactly; wrong ARIA is worse than none.

Every control works with the keyboard alone, the focus is always visible, and focus moves sensibly when a dialog opens
or closes. Text meets the contrast ratios (4.5:1, or 3:1 for large text), targets are at least 24 by 24 pixels, and
motion respects `prefers-reduced-motion`. The `accessibility-audit` skill has the full check.

## Strict types

`strict` stays on in `tsconfig.json`. `any` switches the checker off for everything it touches; use `unknown` and
narrow it. `@ts-ignore` hides the next error too, whatever it turns out to be; if a suppression is unavoidable, use
`@ts-expect-error` with the reason on the same line. A non-null assertion (`value!`) is a promise the compiler cannot
check; on data that can really be absent, handle the absent case.

Props are typed with an explicit type per component. Shared shapes (API responses, events) come from one place,
generated from the backend's schema when it has one, never copied by hand. The `typescript-types` skill covers the
useful advanced types.

## Data at the boundary

The compiler only knows what you told it about the server. Parse responses where they enter the app, with a schema
library or a generated client, so a changed field fails loudly in one place instead of as `undefined` in a component
three levels down. Keep raw `fetch` calls inside the API module; components call typed functions.

## Look at it

Tests prove behaviour, not that the page looks right. After a UI change, run the app with `start_process` and its port
so it shows in the preview beside the chat, or open it with `open_browser`, and look at it at desktop width and at
about 390 pixels. Check the states too: loading, empty, an error, a long name. Say in the reply what you looked at.

## Measured performance

Optimise what a measurement shows: the bundle analyser, Lighthouse or Web Vitals in the field, the React profiler for
slow renders. `useMemo`, `memo` and code splitting each cost readability; add them where a number says they help, and
note the number before and after.

## State

Server data lives in a fetching library's cache, not copied into a store; values that can be computed are computed.
The `react-state` skill explains where each kind of state belongs.
