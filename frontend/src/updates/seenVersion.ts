// What this browser already knows about updates, kept in localStorage: the newest version whose section was opened
// (it clears the dot on Settings) and the newest version a notification was shown for.

const SEEN_KEY = "yaap.updates.seen";
const NOTIFIED_KEY = "yaap.updates.notified";
const CHANGED = "yaap:updates-seen";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage can be blocked; the dot then shows again on the next load, which is harmless.
  }
}

export function seenVersion(): string | null {
  return read(SEEN_KEY);
}

export function markSeen(version: string): void {
  write(SEEN_KEY, version);
  window.dispatchEvent(new Event(CHANGED));
}

export function notifiedVersion(): string | null {
  return read(NOTIFIED_KEY);
}

export function markNotified(version: string): void {
  write(NOTIFIED_KEY, version);
}

/** Calls back when the seen version changes in this tab; returns the function that stops it. */
export function onSeenChanged(callback: () => void): () => void {
  window.addEventListener(CHANGED, callback);
  return () => window.removeEventListener(CHANGED, callback);
}
