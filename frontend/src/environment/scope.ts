// The project the chat sidebar shows, remembered per environment in this browser; null means conversations outside
// a project. Plugin pages get it as the active project.

function scopeKey(environmentId: number): string {
  return `yaap.scope.${environmentId}`;
}

export function readScope(environmentId: number): number | null {
  try {
    const value = window.localStorage.getItem(scopeKey(environmentId));
    return value ? Number(value) : null;
  } catch {
    return null;
  }
}

export function writeScope(environmentId: number, projectId: number | null): void {
  try {
    if (projectId === null) window.localStorage.removeItem(scopeKey(environmentId));
    else window.localStorage.setItem(scopeKey(environmentId), String(projectId));
  } catch {
    // Storage can be unavailable; the scope then resets on reload.
  }
}
