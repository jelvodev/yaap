// The panels beside the chat and how links name them. Pure, so `railPanels.test.mjs` can load it in Node; keep it
// free of imports.

/** The panels of the project rail, top to bottom; Project settings sits at the bottom. */
export const RAIL_PANELS = [
  "changes",
  "files",
  "branches",
  "history",
  "preview",
  "memory",
  "knowledge",
  "tasks",
  "evals",
  "settings",
] as const;

/** A panel of the project rail: Branches holds branches and pull requests; History, Memory and Evals have a switch. */
export type RailPanel = (typeof RAIL_PANELS)[number];

/** The switch inside the merged panels; the first view is the one a panel opens on. */
export const RAIL_VIEWS = {
  history: ["history", "checkpoints"],
  memory: ["memory", "learning"],
  evals: ["evals", "reviews"],
} as const;

type SwitchedPanel = keyof typeof RAIL_VIEWS;

/** The views of one panel: its switch values, or null for a panel without a switch. */
export type RailViewOf<P extends RailPanel> = P extends SwitchedPanel ? (typeof RAIL_VIEWS)[P][number] : null;

/** Any view of a merged panel. */
export type RailView = RailViewOf<SwitchedPanel>;

/** An open panel with its view: `{ panel: "history", view: "checkpoints" }`, `{ panel: "files", view: null }`. */
export type RailLocation = { [P in RailPanel]: { panel: P; view: RailViewOf<P> } }[RailPanel];

/** The tabs of the project panel before the rail. Links (`?panel=`) and the panel's tab row still use them. */
export const PANEL_TABS = [
  "changes",
  "files",
  "branches",
  "history",
  "checkpoints",
  "memory",
  "knowledge",
  "tasks",
  "evals",
  "learning",
  "reviews",
  "project",
] as const;

export type PanelTab = (typeof PANEL_TABS)[number];

/** The value a link uses for a location: an old tab name, or `preview`. */
export type RailLink = PanelTab | "preview";

/** A location for a panel, on the given view when it belongs to the panel, else on the panel's first view. */
export function railLocation(panel: RailPanel, view: RailView | null = null): RailLocation {
  switch (panel) {
    case "history":
      return { panel, view: view === "checkpoints" ? "checkpoints" : "history" };
    case "memory":
      return { panel, view: view === "learning" ? "learning" : "memory" };
    case "evals":
      return { panel, view: view === "reviews" ? "reviews" : "evals" };
    default:
      return { panel, view: null };
  }
}

const TAB_LOCATIONS: Record<PanelTab, RailLocation> = {
  changes: railLocation("changes"),
  files: railLocation("files"),
  branches: railLocation("branches"),
  history: railLocation("history", "history"),
  checkpoints: railLocation("history", "checkpoints"),
  memory: railLocation("memory", "memory"),
  learning: railLocation("memory", "learning"),
  knowledge: railLocation("knowledge"),
  tasks: railLocation("tasks"),
  evals: railLocation("evals", "evals"),
  reviews: railLocation("evals", "reviews"),
  project: railLocation("settings"),
};

function isPanelTab(value: string): value is PanelTab {
  return (PANEL_TABS as readonly string[]).includes(value);
}

function isRailPanel(value: string): value is RailPanel {
  return (RAIL_PANELS as readonly string[]).includes(value);
}

/**
 * The location a link value opens: an old tab name (`checkpoints` opens History on Checkpoints, `project` opens
 * Project settings) or a rail panel's own name (`preview`, `settings`). Null for anything else.
 */
export function locationFromLink(value: string | null): RailLocation | null {
  if (value === null) return null;
  if (isPanelTab(value)) return TAB_LOCATIONS[value];
  if (isRailPanel(value)) return railLocation(value);
  return null;
}

/** The link value for a location, the reverse of `locationFromLink`: the old tab names stay the address. */
export function linkFromLocation(location: RailLocation): RailLink {
  if (location.view !== null) return location.view;
  if (location.panel === "settings") return "project";
  return location.panel;
}

/** The project panel's tab for a location, or null for the preview, which is a panel of its own. */
export function panelTabOf(location: RailLocation | null): PanelTab | null {
  if (location === null) return null;
  const link = linkFromLocation(location);
  return link === "preview" ? null : link;
}
