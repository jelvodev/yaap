/**
 * Every request of the page goes through window.fetch, also the plain fetch calls outside the generated client.
 * This wrapper adds the session's CSRF token to requests that change something, and tells the
 * sign-in gate when the server answers 401, so an ended session shows the sign-in screen instead of errors.
 * It is imported first in main.tsx, before anything else calls fetch.
 */

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
export const SIGNED_OUT_EVENT = "yaap:signed-out";
// Requests whose 401 is an answer, not a lost session.
const AUTH_PATHS = ["/api/v1/auth/state", "/api/v1/auth/sign-in", "/api/v1/auth/setup", "/api/v1/auth/password"];

/** The server puts the port in the cookie names, so two YAAPs on one computer keep their own sessions. */
export function csrfCookieName(): string {
  return window.location.port ? `yaap_csrf_${window.location.port}` : "yaap_csrf";
}

export function csrfToken(): string | null {
  const name = `${csrfCookieName()}=`;
  for (const part of document.cookie.split(";")) {
    const item = part.trim();
    if (item.startsWith(name)) return decodeURIComponent(item.slice(name.length));
  }
  return null;
}

function sameOrigin(url: URL): boolean {
  return url.origin === window.location.origin;
}

const originalFetch = window.fetch.bind(window);

window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const request = input instanceof Request ? input : null;
  const url = new URL(request ? request.url : input.toString(), window.location.href);
  const method = (init?.method ?? request?.method ?? "GET").toUpperCase();
  let nextInit = init;
  if (sameOrigin(url) && !SAFE_METHODS.has(method)) {
    const token = csrfToken();
    if (token) {
      const headers = new Headers(init?.headers ?? request?.headers);
      headers.set("X-CSRF-Token", token);
      nextInit = { ...init, headers };
    }
  }
  const response = await originalFetch(input, nextInit);
  if (response.status === 401 && sameOrigin(url) && url.pathname.startsWith("/api/") && !AUTH_PATHS.includes(url.pathname)) {
    window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
  }
  return response;
};
