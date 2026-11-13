// Tests of the plain request functions behind useScopedSettings (npm run test:unit). Node strips the types as it loads
// the module. The hook itself (the stale-answer guard, state kept on a failed reset) needs a React renderer, which
// the project does not have, so what is checked here is what the hook relies on: which request goes to which scope,
// that a failure is reported and not thrown, and that a Global reset reads the resolved default afterwards.

import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { afterEach, test } from "node:test";

// The module imports "../../api/client" without an extension, as the bundler allows; Node needs the .ts spelled out.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith(".") && !/\.\w+$/.test(specifier)) {
      try {
        return nextResolve(`${specifier}.ts`, context);
      } catch {
        // Not a TypeScript file next to it: resolve the specifier as written.
      }
    }
    return nextResolve(specifier, context);
  },
});

// Node cannot build a Request from a relative URL as a browser does; the client takes the class at import time.
const NativeRequest = globalThis.Request;
globalThis.Request = class extends NativeRequest {
  constructor(input, init) {
    super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
  }
};

const { deleteSetting, listSettings, putSetting } = await import("./useScopedSettings.ts");

const GLOBAL = { kind: "global" };
const PROJECT = { kind: "project", id: 7 };
const ENVIRONMENT = { kind: "environment", id: 3 };

const originalWindow = globalThis.window;
afterEach(() => {
  globalThis.window = originalWindow;
});

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** Answers each request with the next prepared response and records "METHOD path". */
function serve(...answers) {
  const calls = [];
  globalThis.window = {
    fetch: async (request) => {
      calls.push(`${request.method} ${new URL(request.url, "http://localhost").pathname}`);
      const answer = answers.shift();
      assert.ok(answer, `unexpected request: ${calls.at(-1)}`);
      return answer;
    },
  };
  return calls;
}

const DEFAULT = { key: "theme", value: "light", source: "default" };

test("a rejected project reset reports the server's reason and does not throw", async () => {
  const calls = serve(json({ detail: "Resetting is not allowed." }, 409));
  const result = await deleteSetting(PROJECT, "theme");
  assert.deepEqual(result, { ok: false, error: "Resetting is not allowed." });
  assert.deepEqual(calls, ["DELETE /api/v1/projects/7/settings/theme"]);
});

test("a rejected environment reset falls back to a readable message when the server gives none", async () => {
  serve(json({}, 500));
  assert.deepEqual(await deleteSetting(ENVIRONMENT, "theme"), { ok: false, error: "Reset failed." });
});

test("a project reset answers with the setting resolved again", async () => {
  const calls = serve(json(DEFAULT));
  assert.deepEqual(await deleteSetting(PROJECT, "theme"), { ok: true, data: DEFAULT });
  assert.equal(calls.length, 1);
});

test("a Global reset reloads the list to show the resolved default", async () => {
  const other = { key: "language", value: "nl", source: "global" };
  const calls = serve(new Response(null, { status: 204 }), json([other, DEFAULT]));
  assert.deepEqual(await deleteSetting(GLOBAL, "theme"), { ok: true, data: DEFAULT });
  assert.deepEqual(calls, ["DELETE /api/v1/settings/theme", "GET /api/v1/settings"]);
});

test("a rejected Global reset does not reload the list", async () => {
  const calls = serve(json({ detail: "No." }, 403));
  assert.deepEqual(await deleteSetting(GLOBAL, "theme"), { ok: false, error: "No." });
  assert.deepEqual(calls, ["DELETE /api/v1/settings/theme"]);
});

test("a Global reset whose reload fails reports the reload's error", async () => {
  serve(new Response(null, { status: 204 }), json({ detail: "Settings are unavailable." }, 500));
  assert.deepEqual(await deleteSetting(GLOBAL, "theme"), { ok: false, error: "Settings are unavailable." });
});

test("a Global reset of a key missing from the reloaded list reports it", async () => {
  serve(new Response(null, { status: 204 }), json([{ key: "language", value: "nl", source: "global" }]));
  assert.deepEqual(await deleteSetting(GLOBAL, "theme"), {
    ok: false,
    error: "The reset setting could not be loaded.",
  });
});

const REQUESTS = [
  ["global", GLOBAL, "GET /api/v1/settings", "PUT /api/v1/settings/theme"],
  ["environment", ENVIRONMENT, "GET /api/v1/environments/3/settings", "PUT /api/v1/environments/3/settings/theme"],
  ["project", PROJECT, "GET /api/v1/projects/7/settings", "PUT /api/v1/projects/7/settings/theme"],
];

for (const [name, scope, list, put] of REQUESTS) {
  test(`${name} settings are listed and stored at the ${name} level`, async () => {
    const calls = serve(json([DEFAULT]), json(DEFAULT));
    assert.deepEqual(await listSettings(scope), { ok: true, data: [DEFAULT] });
    assert.deepEqual(await putSetting(scope, "theme", "light"), { ok: true, data: DEFAULT });
    assert.deepEqual(calls, [list, put]);
  });
}

test("a failed list and a failed save are reported with their own fallback", async () => {
  serve(json({}, 500), json({}, 500));
  assert.deepEqual(await listSettings(PROJECT), { ok: false, error: "Loading settings failed." });
  assert.deepEqual(await putSetting(PROJECT, "theme", "light"), { ok: false, error: "Saving failed." });
});
