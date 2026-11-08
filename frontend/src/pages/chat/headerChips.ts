// What the chat header's chips say. Pure, so `headerChips.test.mjs` can load it in Node; keep it free of imports.

/** The chip that shows while the project's changes ask for something: review them, push them, or both. */
export interface ChangeChip {
  /** `5 changed · Review`, `1 to push · Sync`, or `5 changed · 1 to push`. */
  label: string;
  /** The longer explanation, for the chip's tooltip. */
  title: string;
  /** Which situation it is, for its style: uncommitted changes weigh more than commits that are not pushed. */
  kind: "changes" | "push" | "both";
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/**
 * The change chip for the project's git state: null when there is nothing to do. Every state opens the Changes panel,
 * where both committing and syncing happen.
 */
export function changeChip(changedFiles: number, ahead: number): ChangeChip | null {
  const changed = Math.max(0, changedFiles);
  const toPush = Math.max(0, ahead);
  const files = plural(changed, "file has", "files have");
  const commits = plural(toPush, "commit is", "commits are");
  if (changed > 0 && toPush > 0) {
    return {
      label: `${changed} changed · ${toPush} to push`,
      title: `${files} uncommitted changes and ${commits} not pushed yet: open Changes`,
      kind: "both",
    };
  }
  if (changed > 0) {
    return {
      label: `${changed} changed · Review`,
      title: `${files} uncommitted changes: review them in Changes`,
      kind: "changes",
    };
  }
  if (toPush > 0) {
    return {
      label: `${toPush} to push · Sync`,
      title: `${commits} not pushed yet: sync them from Changes`,
      kind: "push",
    };
  }
  return null;
}

/** Where the conversation works, as the project chip names it. */
export interface WorkPlace {
  /** The project's name; null outside a project. */
  project: string | null;
  /** The project's branch, once its git state is read. */
  branch: string | null;
  /** The computer the conversation works on over SSH; null in YAAP's own container. */
  target: string | null;
  /** The conversation has full control over that computer. */
  fullControl: boolean;
}

/**
 * The project chip's text: `TESTPROJECT_Yaap · main`, with a computer `… · main · on devbox` (or `full control on
 * devbox`), and outside a project only the computer. Null when there is nothing to name.
 */
export function projectChipLabel(place: WorkPlace): string | null {
  const parts: string[] = [];
  if (place.project) parts.push(place.project);
  if (place.project && place.branch) parts.push(place.branch);
  if (place.target) parts.push(place.fullControl ? `full control on ${place.target}` : `on ${place.target}`);
  if (parts.length === 0) return null;
  const label = parts.join(" · ");
  return place.project ? label : label.charAt(0).toUpperCase() + label.slice(1);
}
