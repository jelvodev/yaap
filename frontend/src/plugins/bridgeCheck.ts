// The checks YAAP's page applies before it makes an API call a plugin page asked for (usePluginCalls). Pure functions
// without imports, so plugin-sdk/bridge.test.mjs runs them under Node as they are. The server holds the same call to
// the manifest again (the X-YAAP-Plugin header, backend/app/plugins/page_access.py); this check keeps a refused call
// from being sent at all and answers the page with the reason.

export type BridgeMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export type QueryItem = string | number | boolean;

/** A call as the SDK posts it: the route as the manifest writes it, its path parameters, a query and a JSON body. */
export interface BridgeCall {
  id: string;
  method: BridgeMethod;
  route: string;
  path: Record<string, string>;
  query: Record<string, QueryItem | QueryItem[]>;
  body: unknown;
}

export type ParsedCall = { ok: true; call: BridgeCall } | { ok: false; id: string | null; problem: string };

const METHODS: readonly string[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];
// Never made for a page, listed or not: the account, sessions and tokens, and the plugin routes.
const RESERVED_PREFIXES: readonly string[] = ["/api/v1/auth", "/api/v1/plugins"];
const API_PREFIX = "/api/v1/";
const MAX_ID_LENGTH = 100;
const PARAMETER = /\{([^}]+)\}/g;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isQueryItem(value: unknown): value is QueryItem {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

function isMethod(value: unknown): value is BridgeMethod {
  return typeof value === "string" && METHODS.includes(value);
}

function stringRecord(value: unknown): Record<string, string> | null {
  if (value === undefined) return {};
  if (!isRecord(value)) return null;
  const result: Record<string, string> = {};
  for (const [name, item] of Object.entries(value)) {
    if (typeof item !== "string") return null;
    result[name] = item;
  }
  return result;
}

function queryRecord(value: unknown): Record<string, QueryItem | QueryItem[]> | null {
  if (value === undefined) return {};
  if (!isRecord(value)) return null;
  const result: Record<string, QueryItem | QueryItem[]> = {};
  for (const [name, item] of Object.entries(value)) {
    if (isQueryItem(item)) result[name] = item;
    else if (Array.isArray(item) && item.every(isQueryItem)) result[name] = [...item];
    else return null;
  }
  return result;
}

/** The fields of a request message (its protocol, version and type already checked), or what is wrong with it. */
export function parseBridgeCall(data: Record<string, unknown>): ParsedCall {
  const id = typeof data.id === "string" && data.id.length > 0 && data.id.length <= MAX_ID_LENGTH ? data.id : null;
  if (id === null) return { ok: false, id: null, problem: "The call has no id." };
  if (!isMethod(data.method)) return { ok: false, id, problem: "The call names no method YAAP's API knows." };
  if (typeof data.path !== "string") return { ok: false, id, problem: "The call names no route." };
  const params = data.params === undefined ? {} : data.params;
  if (!isRecord(params)) return { ok: false, id, problem: "The call's params must be an object." };
  const path = stringRecord(params.path);
  if (path === null) return { ok: false, id, problem: "The call's path parameters must be strings." };
  const query = queryRecord(params.query);
  if (query === null) return { ok: false, id, problem: "The call's query must hold strings, numbers or booleans." };
  return { ok: true, call: { id, method: data.method, route: data.path, path, query, body: data.body } };
}

function parameterNames(route: string): string[] {
  return [...route.matchAll(PARAMETER)].map((match) => match[1]);
}

/** Why YAAP's page refuses to make this call for the plugin, or null when it may. */
export function callRefusal(plugin: string, routes: readonly string[], call: BridgeCall): string | null {
  const { method, route } = call;
  if (!route.startsWith(API_PREFIX)) return "A plugin page can only call YAAP's API routes.";
  if (RESERVED_PREFIXES.some((prefix) => route === prefix || route.startsWith(prefix + "/"))) {
    return "A plugin page cannot call the account, token or plugin routes.";
  }
  if (!routes.includes(`${method} ${route}`)) {
    return `The plugin ${plugin} does not list ${method} ${route} under api in its plugin.json.`;
  }
  const names = parameterNames(route);
  for (const name of names) {
    const value = call.path[name];
    if (value === undefined || value === "") return `${route} needs params.path.${name}.`;
    // A browser resolves "." and ".." segments before the request leaves, which would change the route.
    if (value === "." || value === "..") return `params.path.${name} cannot be "${value}".`;
  }
  const extra = Object.keys(call.path).find((name) => !names.includes(name));
  if (extra !== undefined) return `${route} has no path parameter ${extra}.`;
  return null;
}

/** The address of a checked call: the route with its path parameters encoded, and the query. */
export function callAddress(call: BridgeCall): string {
  const path = call.route.replace(PARAMETER, (_, name: string) => encodeURIComponent(call.path[name] ?? ""));
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(call.query)) {
    for (const item of Array.isArray(value) ? value : [value]) search.append(name, String(item));
  }
  const text = search.toString();
  return text ? `${path}?${text}` : path;
}
