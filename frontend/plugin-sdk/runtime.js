// The runtime of YAAP's plugin SDK. generate.mjs fills in the three tables below from the OpenAPI description and
// protocol.json, and the frontend build serves the result as /plugin-sdk.js. A plugin page imports it as an ES module:
//
//   import { connect } from "/plugin-sdk.js";
//   const yaap = await connect();
//
// The page runs in a sandboxed frame on an opaque origin: it has no session and no token, and YAAP refuses its own
// requests to the API. Every call goes by postMessage to YAAP's page around the frame, which checks it against the
// routes the plugin's manifest lists, makes it with the user's session and posts the answer back.

const API_VERSION = __API_VERSION__;
const ROUTES = new Set(__ROUTES__);
const PROTOCOL = __PROTOCOL__;

const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];
const DEFAULT_TIMEOUT_MS = 10_000;
const DEFAULT_CALL_TIMEOUT_MS = 30_000;
const HELLO_EVERY_MS = 250;

function isHostMessage(data, type) {
  return (
    typeof data === "object" &&
    data !== null &&
    data.protocol === PROTOCOL.name &&
    data.version === PROTOCOL.version &&
    (type === undefined || data.type === type)
  );
}

function frameMessage(type, fields = {}) {
  return { protocol: PROTOCOL.name, version: PROTOCOL.version, type, ...fields };
}

/** Ask the YAAP page around the frame for the page's context, until it answers or the time is up. */
function handshake(host, timeoutMs) {
  return new Promise((resolve, reject) => {
    // The frame's own origin is opaque and YAAP's is not known here, so the target is "*"; the hello carries
    // nothing secret, and YAAP answers only the frame it shows.
    const hello = () => host.postMessage(frameMessage(PROTOCOL.messages.hello), "*");
    const finish = () => {
      clearInterval(repeat);
      clearTimeout(timer);
      window.removeEventListener("message", listen);
    };
    const listen = (event) => {
      if (event.source !== host || !isHostMessage(event.data, PROTOCOL.messages.init)) return;
      finish();
      resolve(event.data);
    };
    window.addEventListener("message", listen);
    const repeat = setInterval(hello, HELLO_EVERY_MS);
    const timer = setTimeout(() => {
      finish();
      reject(new Error("YAAP did not answer. Open this page from Settings › Plugins, where YAAP shows it."));
    }, timeoutMs);
    hello();
  });
}

/** Put YAAP's theme tokens on the page as CSS custom properties, so `var(--text)` and the like work. */
export function applyTheme(theme, root = document.documentElement) {
  for (const [name, value] of Object.entries(theme.tokens)) root.style.setProperty(name, value);
  root.style.colorScheme = theme.scheme;
  root.dataset.theme = theme.scheme;
}

/** The path parameters a route needs, as strings; a TypeError names the first one that is missing. */
function pathValues(template, values = {}) {
  const filled = {};
  for (const [, name] of template.matchAll(/\{([^}]+)\}/g)) {
    const value = values[name];
    if (value === undefined || value === null || value === "") {
      throw new TypeError(`${template} needs params.path.${name}.`);
    }
    filled[name] = String(value);
  }
  return filled;
}

function isQueryItem(value) {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

/** The query without empty values; strings, numbers, booleans and lists of them. */
function queryValues(query = {}) {
  const kept = {};
  for (const [name, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    const items = Array.isArray(value) ? value : [value];
    if (!items.every(isQueryItem)) throw new TypeError(`params.query.${name} must be a string, number or boolean.`);
    kept[name] = Array.isArray(value) ? [...value] : value;
  }
  return kept;
}

/**
 * A client for YAAP's API through the YAAP page around this frame. Every call names a route as YAAP's OpenAPI
 * description writes it, such as "/api/v1/environments/{environment_id}/projects", and returns { data, status } or
 * { error, status }. connect() makes one; createClient is for pages that want their own timeout.
 */
export function createClient({ host = window.parent, timeoutMs = DEFAULT_CALL_TIMEOUT_MS } = {}) {
  const pending = new Map();
  let counter = 0;
  window.addEventListener("message", (event) => {
    if (event.source !== host || !isHostMessage(event.data, PROTOCOL.messages.response)) return;
    pending.get(event.data.id)?.(event.data);
  });

  function request(method, path, init = {}) {
    if (!ROUTES.has(`${method} ${path}`)) {
      return Promise.reject(new TypeError(`${method} ${path} is not a route of YAAP's API ${API_VERSION}.`));
    }
    let params;
    try {
      params = { path: pathValues(path, init.params?.path), query: queryValues(init.params?.query) };
    } catch (error) {
      return Promise.reject(error);
    }
    counter += 1;
    const id = `${Date.now().toString(36)}-${counter}`;
    return new Promise((resolve, reject) => {
      const signal = init.signal;
      const finish = () => {
        pending.delete(id);
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
      };
      const abort = () => {
        finish();
        reject(signal.reason ?? new Error("The call was aborted."));
      };
      const timer = setTimeout(() => {
        finish();
        reject(new Error(`YAAP did not answer ${method} ${path} within ${timeoutMs} ms.`));
      }, timeoutMs);
      if (signal?.aborted) {
        abort();
        return;
      }
      signal?.addEventListener("abort", abort);
      pending.set(id, (answer) => {
        finish();
        const status = typeof answer.status === "number" ? answer.status : 0;
        if (answer.ok === true) resolve({ data: answer.data, status });
        else resolve({ error: answer.error ?? { detail: `HTTP ${status}` }, status });
      });
      try {
        host.postMessage(frameMessage(PROTOCOL.messages.request, { id, method, path, params, body: init.body }), "*");
      } catch (error) {
        // A body that cannot be copied into a message (a function, a DOM node).
        finish();
        reject(error);
      }
    });
  }
  const calls = METHODS.map((method) => [method, (path, init) => request(method, path, init)]);
  return Object.freeze(Object.fromEntries(calls));
}

/**
 * Connect to the YAAP page that shows this one: get the plugin's routes, the active environment and project and the
 * theme, and follow their changes. Resolves with the connection; rejects when the page is not shown by YAAP.
 */
export async function connect(options = {}) {
  const host = window.parent;
  if (!host || host === window) {
    throw new Error("This page runs inside YAAP. Open it from Settings › Plugins.");
  }
  const init = await handshake(host, options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const state = { environment: init.environment, project: init.project, theme: init.theme };
  const listeners = { context: new Set(), theme: new Set() };
  const autoTheme = options.applyTheme !== false;
  if (autoTheme) applyTheme(state.theme);

  window.addEventListener("message", (event) => {
    if (event.source !== host || !isHostMessage(event.data)) return;
    if (event.data.type === PROTOCOL.messages.context) {
      state.environment = event.data.environment;
      state.project = event.data.project;
      const context = { environment: state.environment, project: state.project };
      for (const handler of listeners.context) handler(context);
    } else if (event.data.type === PROTOCOL.messages.theme) {
      state.theme = event.data.theme;
      if (autoTheme) applyTheme(state.theme);
      for (const handler of listeners.theme) handler(state.theme);
    }
  });

  return Object.freeze({
    plugin: init.plugin,
    routes: Object.freeze([...init.routes]),
    apiVersion: API_VERSION,
    get environment() {
      return state.environment;
    },
    get project() {
      return state.project;
    },
    get theme() {
      return state.theme;
    },
    api: createClient({ host, timeoutMs: options.callTimeoutMs ?? DEFAULT_CALL_TIMEOUT_MS }),
    on(event, handler) {
      const set = listeners[event];
      if (!set) throw new TypeError(`Unknown event ${event}; use "context" or "theme".`);
      set.add(handler);
      return () => set.delete(handler);
    },
  });
}
