/**
 * The API calls YAAP's page makes for a plugin page (usePluginCalls). The page in the sandboxed frame names a route
 * from its manifest; which route that is, is known only at run time, so this cannot go through the generated client,
 * whose paths are checked at compile time. The route has been checked against the manifest (bridgeCheck.ts) before
 * it gets here, the call goes through window.fetch so auth-fetch.ts adds the CSRF token, and the X-YAAP-Plugin header
 * lets the server hold it to the manifest as well. The answer is passed on to the page as it came, never cast.
 */

export const PLUGIN_HEADER = "X-YAAP-Plugin";

export type PluginCallResult =
  | { ok: true; status: number; data: unknown }
  | { ok: false; status: number; error: unknown };

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  if ((response.headers.get("content-type") ?? "").includes("json")) {
    try {
      const parsed: unknown = JSON.parse(text);
      return parsed;
    } catch {
      return text;
    }
  }
  return text;
}

/** Make a checked call for a plugin with the session, and return its outcome as the SDK passes it to the page. */
export async function callForPlugin(
  plugin: string,
  method: string,
  address: string,
  body: unknown,
): Promise<PluginCallResult> {
  const headers: Record<string, string> = { Accept: "application/json", [PLUGIN_HEADER]: plugin };
  let payload: string | undefined;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    try {
      payload = JSON.stringify(body);
    } catch {
      return { ok: false, status: 400, error: { detail: "The call's body cannot be sent as JSON." } };
    }
  }
  let response: Response;
  try {
    response = await window.fetch(address, {
      method,
      headers,
      body: payload,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    return { ok: false, status: 0, error: { detail: "YAAP could not be reached." } };
  }
  const content = await readBody(response);
  if (response.ok) return { ok: true, status: response.status, data: content };
  // YAAP refuses with { detail: "why" }; anything else (a proxy's page) is wrapped the same way.
  const error =
    typeof content === "object" && content !== null
      ? content
      : { detail: content || response.statusText || `HTTP ${response.status}` };
  return { ok: false, status: response.status, error };
}
