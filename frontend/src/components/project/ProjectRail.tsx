import type { ComponentType } from "react";

import { RAIL_ITEMS, anyRailBadge, railBadge, railItemLabel } from "../../pages/chat/railItems";
import { RAIL_PANELS, type RailPanel } from "../../pages/chat/railPanels";
import type { ProjectRail as RailState } from "../../pages/chat/useProjectRail";
import { useRailMode } from "../../pages/chat/useRailMode";
import {
  BookIcon,
  BranchIcon,
  BulbIcon,
  CloseIcon,
  CommitIcon,
  FlaskIcon,
  FolderIcon,
  HistoryIcon,
  ListIcon,
  PreviewIcon,
  SettingsIcon,
  SidePanelIcon,
} from "../icons";
import "./project-rail.css";

const ICONS: Record<RailPanel, ComponentType<{ size?: number }>> = {
  changes: CommitIcon,
  files: FolderIcon,
  branches: BranchIcon,
  history: HistoryIcon,
  preview: PreviewIcon,
  memory: BulbIcon,
  knowledge: BookIcon,
  tasks: ListIcon,
  evals: FlaskIcon,
  settings: SettingsIcon,
};

/** The icons after which the rail leaves a little room, as in the mockup: git, the preview, what the agent knows. */
const GROUP_ENDS: ReadonlySet<RailPanel> = new Set(["history", "preview", "knowledge"]);

/** The icons above the gap; Project settings sits at the bottom. */
const TOP_PANELS = RAIL_PANELS.filter((panel) => panel !== "settings");

interface ProjectRailProps {
  rail: RailState;
  /** The preview can open: the open conversation works in a project (a new conversation has no processes yet). */
  previewAvailable: boolean;
}

/**
 * The icon rail on the right edge of the chat: one icon per panel, a click opens its panel beside the
 * chat and a second click closes it. On a narrow window it folds into one button that opens it as a drawer.
 */
export default function ProjectRail({ rail, previewAvailable }: ProjectRailProps) {
  const { mode, unfold, fold } = useRailMode(rail.active !== null);
  const closeDrawer = () => {
    rail.close();
    fold();
  };

  function item(panel: RailPanel) {
    const Icon = ICONS[panel];
    const badge = railBadge(panel, rail.counts);
    const disabled = panel === "preview" && !previewAvailable;
    const label = disabled ? "Preview (in a conversation of this project)" : railItemLabel(panel, rail.counts);
    return (
      <button
        key={panel}
        type="button"
        className={GROUP_ENDS.has(panel) ? "rail-item rail-item-group-end" : "rail-item"}
        aria-pressed={rail.panel === panel}
        aria-label={label}
        title={disabled ? label : RAIL_ITEMS[panel].label}
        disabled={disabled}
        onClick={() => {
          rail.toggle(panel);
          // In the drawer the rail stays when its panel closes.
          if (mode === "drawer") unfold();
        }}
      >
        <Icon size={17} />
        {badge?.kind === "count" && (
          <span className="rail-count" aria-hidden="true">
            {badge.text}
          </span>
        )}
        {badge?.kind === "dot" && <span className="rail-dot" aria-hidden="true" />}
      </button>
    );
  }

  return (
    <>
      {mode === "folded" && (
        <button
          type="button"
          className="icon-button rail-fold"
          aria-label="Open the project rail"
          title="Project panels"
          onClick={unfold}
        >
          <SidePanelIcon size={17} />
          {anyRailBadge(RAIL_PANELS, rail.counts) && <span className="rail-dot" aria-hidden="true" />}
        </button>
      )}
      {mode === "drawer" && <div className="rail-backdrop" aria-hidden="true" onClick={closeDrawer} />}
      <nav
        className={`project-rail project-rail-${mode}`}
        aria-label="Project panels"
        onKeyDown={(event) => {
          if (mode === "drawer" && event.key === "Escape") closeDrawer();
        }}
      >
        {mode === "drawer" && (
          <button type="button" className="rail-item" aria-label="Close the project rail" onClick={closeDrawer}>
            <CloseIcon size={17} />
          </button>
        )}
        {TOP_PANELS.map(item)}
        <span className="rail-gap" />
        {item("settings")}
      </nav>
    </>
  );
}
