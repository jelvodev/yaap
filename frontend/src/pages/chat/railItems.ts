// What the project rail shows for each panel: its name, the title of its panel, the labels of its switch, the badge on
// its icon, and whether the rail folds. Pure, so `railItems.test.mjs` can load it in Node; it imports only types.

import type { RailPanel, RailView } from "./railPanels";

/** The rail icon's name (its tooltip and aria-label) and the title in the panel's header. */
export const RAIL_ITEMS: Record<RailPanel, { label: string; title: string }> = {
  changes: { label: "Changes", title: "Changes" },
  files: { label: "Files", title: "Files" },
  branches: { label: "Branches and pull requests", title: "Branches" },
  history: { label: "History and checkpoints", title: "History" },
  preview: { label: "Preview", title: "Preview" },
  memory: { label: "Memory and learning", title: "Memory" },
  knowledge: { label: "Knowledge", title: "Knowledge" },
  tasks: { label: "Tasks", title: "Tasks" },
  evals: { label: "Evals and reviews", title: "Evals" },
  settings: { label: "Project settings", title: "Project settings" },
};

/** The labels of the switch inside the merged panels. */
export const RAIL_VIEW_LABELS: Record<RailView, string> = {
  history: "History",
  checkpoints: "Checkpoints",
  memory: "Memory",
  learning: "Learning",
  evals: "Evals",
  reviews: "Reviews",
};

/** The counts the badges show; the same shape as `RailCounts` of `useProjectRail`. */
export interface RailBadgeCounts {
  changedFiles: number;
  previewRunning: boolean;
  openProposals: number;
}

/** A badge on a rail icon: a number, or a green dot; `label` is read out with the icon's name. */
export type RailBadge = { kind: "count"; text: string; label: string } | { kind: "dot"; label: string };

/** Counts above this show as `99+`. */
const MAX_SHOWN = 99;

function countBadge(count: number, one: string, many: string): RailBadge | null {
  if (count <= 0) return null;
  const text = count > MAX_SHOWN ? `${MAX_SHOWN}+` : String(count);
  return { kind: "count", text, label: `${count} ${count === 1 ? one : many}` };
}

/**
 * The badge on a panel's icon: Changes counts the changed files, Memory and learning the open proposals, and Preview
 * has a green dot while a process with a port runs. Null when there is nothing to show.
 */
export function railBadge(panel: RailPanel, counts: RailBadgeCounts): RailBadge | null {
  switch (panel) {
    case "changes":
      return countBadge(counts.changedFiles, "changed file", "changed files");
    case "memory":
      return countBadge(counts.openProposals, "open proposal", "open proposals");
    case "preview":
      return counts.previewRunning ? { kind: "dot", label: "a process with a port runs" } : null;
    default:
      return null;
  }
}

/** The icon's accessible name: its label, with what its badge says. */
export function railItemLabel(panel: RailPanel, counts: RailBadgeCounts): string {
  const badge = railBadge(panel, counts);
  return badge ? `${RAIL_ITEMS[panel].label}, ${badge.label}` : RAIL_ITEMS[panel].label;
}

/** Whether any icon has a badge, so the folded rail's button can show a dot. */
export function anyRailBadge(panels: readonly RailPanel[], counts: RailBadgeCounts): boolean {
  return panels.some((panel) => railBadge(panel, counts) !== null);
}

/**
 * Below this width the rail folds into one button (choice 4 of `plan-chat-interface.md`). The same width is the
 * `max-width: 899px` media query of `project-rail.css`, which places the panels as a drawer.
 */
export const RAIL_NARROW_QUERY = "(max-width: 899px)";

/**
 * How the rail shows: `rail` beside the chat on a wide window; on a narrow one `folded` into a button, or as a
 * `drawer` over the chat while the button was used or a panel is open (a link or the header can open one).
 */
export type RailMode = "rail" | "folded" | "drawer";

export function railMode(narrow: boolean, unfolded: boolean, panelOpen: boolean): RailMode {
  if (!narrow) return "rail";
  return unfolded || panelOpen ? "drawer" : "folded";
}
