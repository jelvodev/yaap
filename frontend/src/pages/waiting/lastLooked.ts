// When the user last looked at Waiting for me, per environment. Kept in this browser only: no stored place on the
// server fits it, and a cleared or blocked storage only means every Inbox card shows as new again.

/** Sent on the window after the moment changed, so the sidebar's count follows at once. */
export const LOOKED_EVENT = "yaap:waiting-looked";

function storageKey(environmentId: number): string {
  return `yaap.waiting.lastLooked.${environmentId}`;
}

/** The server's time of the last look, as the server wrote it; null when never or when storage is unavailable. */
export function readLastLooked(environmentId: number): string | null {
  try {
    const value = window.localStorage.getItem(storageKey(environmentId));
    return value && !Number.isNaN(Date.parse(value)) ? value : null;
  } catch {
    return null;
  }
}

export function writeLastLooked(environmentId: number, moment: string): void {
  try {
    window.localStorage.setItem(storageKey(environmentId), moment);
  } catch {
    // Storage is blocked or full: the next look shows the same Inbox cards as new, which is harmless.
    return;
  }
  window.dispatchEvent(new Event(LOOKED_EVENT));
}
