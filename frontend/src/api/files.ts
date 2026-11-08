/** Helpers for a project's files in the interface. */

/** The file itself: images and PDFs are shown inline, anything else downloads. */
export function rawFileUrl(projectId: number, path: string): string {
  return `/api/v1/projects/${projectId}/files/raw?path=${encodeURIComponent(path)}`;
}

/** "5 min ago" for recent changes, else the date. */
export function changedAgo(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  const seconds = (Date.now() - date.getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} d ago`;
  return date.toLocaleDateString();
}
