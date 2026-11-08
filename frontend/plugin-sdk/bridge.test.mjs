// Tests of the checks YAAP's page applies before it makes a call a plugin page asked for (npm run test:sdk). They run
// the frontend's own module, src/plugins/bridgeCheck.ts, under Node, which strips its types.

import assert from "node:assert/strict";
import { test } from "node:test";

import { callAddress, callRefusal, parseBridgeCall } from "../src/plugins/bridgeCheck.ts";

const ROUTES = [
  "GET /api/v1/environments",
  "GET /api/v1/environments/{environment_id}/projects",
  "PUT /api/v1/settings/{key}",
];

function call(method, route, path = {}, query = {}, body = undefined) {
  return { id: "1", method, route, path, query, body };
}

const refusals = [
  // Listed routes with their parameters are made.
  [call("GET", "/api/v1/environments"), null],
  [call("GET", "/api/v1/environments/{environment_id}/projects", { environment_id: "7" }), null],
  [call("PUT", "/api/v1/settings/{key}", { key: "interface" }, {}, { value: {} }), null],
  // Anything the manifest does not list is refused, also another method on a listed path.
  [call("POST", "/api/v1/environments"), /does not list POST \/api\/v1\/environments under api/],
  [call("DELETE", "/api/v1/settings/{key}", { key: "interface" }), /does not list DELETE/],
  [call("GET", "/api/v1/settings"), /The plugin projects does not list GET \/api\/v1\/settings/],
  [call("GET", "/api/v1/environments/7/projects"), /does not list/],
  // Reserved and foreign routes are refused even when listed.
  [call("GET", "/api/v1/auth/tokens"), /account, token or plugin/],
  [call("POST", "/api/v1/plugins/restart"), /account, token or plugin/],
  [call("GET", "/api/v1/plugins"), /account, token or plugin/],
  [call("POST", "/mcp"), /only call YAAP's API/],
  [call("GET", "https://evil.example/api/v1/environments"), /only call YAAP's API/],
  // Path parameters must be present, safe as a segment, and named by the route.
  [call("GET", "/api/v1/environments/{environment_id}/projects"), /needs params.path.environment_id/],
  [call("GET", "/api/v1/environments/{environment_id}/projects", { environment_id: "" }), /needs params.path/],
  [call("GET", "/api/v1/environments/{environment_id}/projects", { environment_id: ".." }), /cannot be "\.\."/],
  [call("GET", "/api/v1/environments/{environment_id}/projects", { environment_id: "." }), /cannot be "\."/],
  [call("GET", "/api/v1/environments", { environment_id: "1" }), /has no path parameter environment_id/],
];

for (const [given, expected] of refusals) {
  test(`${given.method} ${given.route} ${JSON.stringify(given.path)} is ${expected ? "refused" : "made"}`, () => {
    const listed = [...ROUTES, "GET /api/v1/auth/tokens", "POST /api/v1/plugins/restart", "GET /api/v1/plugins"];
    const refusal = callRefusal("projects", listed, given);
    if (expected === null) assert.equal(refusal, null);
    else assert.match(refusal ?? "", expected);
  });
}

const OK = { ok: true };
const refused = (id, problem) => ({ ok: false, id, problem });

const messages = [
  [{ id: "a", method: "GET", path: "/api/v1/environments" }, { ok: true }],
  [{ id: "a", method: "GET", path: "/api/v1/x", params: { path: { a: "1" }, query: { b: [1, true, "c"] } } }, OK],
  [{ method: "GET", path: "/api/v1/environments" }, { ok: false, id: null, problem: /no id/ }],
  [{ id: "x".repeat(101), method: "GET", path: "/api/v1/environments" }, { ok: false, id: null, problem: /no id/ }],
  [{ id: "a", method: "OPTIONS", path: "/api/v1/environments" }, { ok: false, id: "a", problem: /no method/ }],
  [{ id: "a", method: "GET" }, { ok: false, id: "a", problem: /no route/ }],
  [{ id: "a", method: "GET", path: "/api/v1/x", params: [] }, { ok: false, id: "a", problem: /params must be/ }],
  [{ id: "a", method: "GET", path: "/api/v1/x", params: { path: { a: 1 } } }, refused("a", /strings/)],
  [
    { id: "a", method: "GET", path: "/api/v1/x", params: { query: { a: { b: 1 } } } },
    { ok: false, id: "a", problem: /query must hold/ },
  ],
];

for (const [data, expected] of messages) {
  test(`the message ${JSON.stringify(data).slice(0, 80)} is ${expected.ok ? "read" : "refused"}`, () => {
    const parsed = parseBridgeCall(data);
    assert.equal(parsed.ok, expected.ok);
    if (!expected.ok) {
      assert.equal(parsed.id, expected.id);
      assert.match(parsed.problem, expected.problem);
    }
  });
}

test("a checked call's address encodes its path parameters and adds the query", () => {
  const parsed = parseBridgeCall({
    id: "a",
    method: "GET",
    path: "/api/v1/environments/{environment_id}/search",
    params: { path: { environment_id: "a b/c" }, query: { q: "x&y", tag: ["1", "2"], open: false } },
  });
  assert.ok(parsed.ok);
  assert.equal(callAddress(parsed.call), "/api/v1/environments/a%20b%2Fc/search?q=x%26y&tag=1&tag=2&open=false");
  assert.equal(callAddress(call("GET", "/api/v1/environments")), "/api/v1/environments");
});
