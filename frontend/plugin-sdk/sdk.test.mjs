// Tests of the generated plugin SDK (npm run test:sdk): it is generated from openapi.json, it gets its context from
// the YAAP page around the frame, and it sends its API calls to that page by postMessage instead of calling YAAP.

import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { routesOf, writePluginSdk } from "./generate.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OPENAPI = path.join(HERE, "..", "openapi.json");
const PROTOCOL = JSON.parse(await readFile(path.join(HERE, "protocol.json"), "utf8"));
const PROJECTS = [{ id: 3, name: "Website" }];

let folder;
let sdk;

/**
 * A frame's window and its parent (the YAAP page), enough for the SDK's postMessage traffic. The parent answers the
 * hello with init and every call with `reply(call)`, unless told not to.
 */
function fakeFrame({ answer = true, reply = () => ({ ok: true, status: 200, data: PROJECTS }) } = {}) {
  const listeners = new Set();
  const frame = {
    addEventListener: (type, listener) => type === "message" && listeners.add(listener),
    removeEventListener: (type, listener) => type === "message" && listeners.delete(listener),
  };
  const sent = [];
  const calls = [];
  const parent = {
    postMessage(message, target) {
      structuredClone(message);
      sent.push({ message, target });
      if (!answer) return;
      if (message.type === PROTOCOL.messages.hello) queueMicrotask(() => deliver(parent, init()));
      if (message.type === PROTOCOL.messages.request) {
        calls.push(message);
        const outcome = reply(message);
        const response = hostMessage(PROTOCOL.messages.response, { id: message.id, ...outcome });
        if (outcome) queueMicrotask(() => deliver(parent, response));
      }
    },
  };
  function deliver(source, data) {
    for (const listener of [...listeners]) listener({ source, data });
  }
  frame.parent = parent;
  return { frame, parent, sent, calls, deliver, listeners };
}

function hostMessage(type, fields) {
  return { protocol: PROTOCOL.name, version: PROTOCOL.version, type, ...fields };
}

function init() {
  return hostMessage(PROTOCOL.messages.init, {
    plugin: "projects",
    routes: ["GET /api/v1/environments/{environment_id}/projects"],
    environment: { id: 1, name: "Personal", colour: "#5B8CFF" },
    project: null,
    theme: { scheme: "dark", tokens: { "--bg": "#0a0c10", "--accent": "#5B8CFF" } },
  });
}

function fakeDocument() {
  const properties = {};
  const root = {
    dataset: {},
    style: {
      colorScheme: "",
      setProperty: (name, value) => {
        properties[name] = value;
      },
    },
  };
  return { document: { documentElement: root }, properties, root };
}

/** No call may reach the network: the frame has no session and YAAP refuses its origin. */
function forbidFetch() {
  const fetched = [];
  globalThis.fetch = async (url) => {
    fetched.push(url);
    throw new Error("The SDK must not call fetch.");
  };
  return fetched;
}

before(async () => {
  folder = await mkdtemp(path.join(tmpdir(), "yaap-plugin-sdk-"));
  await writePluginSdk(OPENAPI, folder);
  sdk = await import(pathToFileURL(path.join(folder, "plugin-sdk.js")).href);
});

after(async () => {
  await rm(folder, { recursive: true, force: true });
});

test("the SDK is generated from the OpenAPI description, with every route in it", async () => {
  const openapi = JSON.parse(await readFile(OPENAPI, "utf8"));
  const source = await readFile(path.join(folder, "plugin-sdk.js"), "utf8");
  const declarations = await readFile(path.join(folder, "plugin-sdk.d.ts"), "utf8");
  const routes = routesOf(openapi);
  assert.ok(routes.includes("GET /api/v1/environments/{environment_id}/projects"));
  for (const route of routes) assert.ok(source.includes(JSON.stringify(route)), route);
  assert.match(declarations, /export interface paths/);
  assert.match(declarations, /export declare function connect/);
  assert.doesNotMatch(source + declarations, /__[A-Z_]+__/);
  assert.doesNotMatch(source, /fetch\(|Authorization|yaap_page_/);
});

test("connect gets the context from the YAAP page and sends calls to it by postMessage, not by fetch", async () => {
  const { frame, sent, calls } = fakeFrame();
  const { document, properties, root } = fakeDocument();
  globalThis.window = frame;
  globalThis.document = document;
  const fetched = forbidFetch();

  const yaap = await sdk.connect();
  assert.equal(sent[0].message.type, PROTOCOL.messages.hello);
  assert.equal(sent[0].target, "*");
  assert.equal(yaap.plugin, "projects");
  assert.deepEqual(yaap.routes, ["GET /api/v1/environments/{environment_id}/projects"]);
  assert.deepEqual(yaap.environment, { id: 1, name: "Personal", colour: "#5B8CFF" });
  assert.equal(yaap.project, null);
  // The theme tokens are on the page at once.
  assert.equal(properties["--bg"], "#0a0c10");
  assert.equal(root.style.colorScheme, "dark");

  const { data, error, status } = await yaap.api.GET("/api/v1/environments/{environment_id}/projects", {
    params: { path: { environment_id: 1 }, query: { archived: false, tag: ["a", "b"], none: undefined } },
  });
  assert.equal(error, undefined);
  assert.equal(status, 200);
  assert.deepEqual(data, PROJECTS);
  assert.equal(fetched.length, 0);
  assert.equal(calls.length, 1);
  const call = calls[0];
  assert.equal(call.method, "GET");
  assert.equal(call.path, "/api/v1/environments/{environment_id}/projects");
  assert.deepEqual(call.params, { path: { environment_id: "1" }, query: { archived: false, tag: ["a", "b"] } });
  assert.equal(call.body, undefined);
  assert.equal(typeof call.id, "string");
  assert.equal(sent.at(-1).target, "*");
});

test("a JSON body travels with the call, and a refusal comes back as the error", async () => {
  const refusal = { detail: "The plugin projects does not list PUT /api/v1/settings/{key} under api." };
  const { frame, calls } = fakeFrame({ reply: () => ({ ok: false, status: 403, error: refusal }) });
  globalThis.window = frame;
  globalThis.document = fakeDocument().document;
  const yaap = await sdk.connect({ applyTheme: false });

  const { data, error, status } = await yaap.api.PUT("/api/v1/settings/{key}", {
    params: { path: { key: "a b" } },
    body: { value: { theme: "light" } },
  });
  assert.equal(data, undefined);
  assert.equal(status, 403);
  assert.match(error.detail, /does not list/);
  assert.deepEqual(calls[0].params.path, { key: "a b" });
  assert.deepEqual(calls[0].body, { value: { theme: "light" } });
});

test("answers are matched to their calls by id, and answers from elsewhere are ignored", async () => {
  const { frame, parent, calls, deliver } = fakeFrame({ reply: () => undefined });
  globalThis.window = frame;
  const client = sdk.createClient({ host: parent, timeoutMs: 1000 });
  const first = client.GET("/api/v1/environments");
  const second = client.GET("/api/v1/settings");
  assert.equal(calls.length, 2);
  assert.notEqual(calls[0].id, calls[1].id);
  const answer = (id, data) => hostMessage(PROTOCOL.messages.response, { id, ok: true, status: 200, data });
  deliver({}, answer(calls[1].id, "forged"));
  deliver(parent, answer(calls[1].id, "settings"));
  deliver(parent, answer(calls[0].id, "environments"));
  deliver(parent, answer(calls[0].id, "late duplicate"));
  assert.equal((await first).data, "environments");
  assert.equal((await second).data, "settings");
});

test("routes that are not in YAAP's API and missing path parameters fail before any message", async () => {
  const { frame, parent, calls } = fakeFrame();
  globalThis.window = frame;
  const client = sdk.createClient({ host: parent });
  await assert.rejects(client.GET("/api/v1/nothing-here"), /is not a route of YAAP's API/);
  await assert.rejects(client.GET("/api/v1/environments/{environment_id}/projects"), /needs params.path.environment_id/);
  await assert.rejects(
    client.GET("/api/v1/environments", { params: { query: { filter: { nested: true } } } }),
    /must be a string, number or boolean/,
  );
  assert.equal(calls.length, 0);
});

test("a call without an answer times out, and an aborted call rejects", async () => {
  const { frame, parent } = fakeFrame({ reply: () => undefined });
  globalThis.window = frame;
  const client = sdk.createClient({ host: parent, timeoutMs: 30 });
  await assert.rejects(client.GET("/api/v1/environments"), /did not answer GET \/api\/v1\/environments/);
  const controller = new AbortController();
  const aborted = sdk.createClient({ host: parent }).GET("/api/v1/environments", { signal: controller.signal });
  controller.abort(new Error("stopped by the page"));
  await assert.rejects(aborted, /stopped by the page/);
  // A body that cannot be copied into a message fails the call instead of hanging.
  await assert.rejects(client.POST("/api/v1/environments", { body: { name: () => "x" } }));
});

test("context and theme changes from the YAAP page reach the handlers; messages from elsewhere do not", async () => {
  const { frame, parent, deliver } = fakeFrame();
  const { document, properties } = fakeDocument();
  globalThis.window = frame;
  globalThis.document = document;
  const yaap = await sdk.connect();
  const contexts = [];
  const themes = [];
  const stop = yaap.on("context", (context) => contexts.push(context));
  yaap.on("theme", (theme) => themes.push(theme));

  const project = { id: 3, name: "Website" };
  deliver(parent, hostMessage(PROTOCOL.messages.context, { environment: yaap.environment, project }));
  deliver({}, hostMessage(PROTOCOL.messages.context, { environment: yaap.environment, project: null }));
  deliver(parent, hostMessage(PROTOCOL.messages.theme, { theme: { scheme: "light", tokens: { "--bg": "#f4f5f8" } } }));
  assert.deepEqual(contexts, [{ environment: yaap.environment, project }]);
  assert.deepEqual(yaap.project, project);
  assert.equal(themes[0].scheme, "light");
  assert.equal(properties["--bg"], "#f4f5f8");

  stop();
  deliver(parent, hostMessage(PROTOCOL.messages.context, { environment: yaap.environment, project: null }));
  assert.equal(contexts.length, 1);
  assert.throws(() => yaap.on("message", () => undefined), /Unknown event/);
});

test("connect fails clearly when no YAAP page answers", async () => {
  const { frame } = fakeFrame({ answer: false });
  globalThis.window = frame;
  await assert.rejects(sdk.connect({ timeoutMs: 50 }), /Open this page from Settings › Plugins/);

  const alone = { addEventListener() {}, removeEventListener() {} };
  alone.parent = alone;
  globalThis.window = alone;
  await assert.rejects(sdk.connect(), /runs inside YAAP/);
});
