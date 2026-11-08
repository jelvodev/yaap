import type { ReactNode } from "react";

import { RAIL_ITEMS, RAIL_VIEW_LABELS } from "../../pages/chat/railItems";
import { RAIL_VIEWS, type RailLocation, type RailView } from "../../pages/chat/railPanels";
import { CloseIcon } from "../icons";
import { Segmented } from "../workbench/Workbench";

/** The switch values of a merged panel, or null for a panel without a switch. */
function viewsOf(location: RailLocation): readonly RailView[] | null {
  switch (location.panel) {
    case "history":
    case "memory":
    case "evals":
      return RAIL_VIEWS[location.panel];
    default:
      return null;
  }
}

/**
 * The header of a panel of the project rail: its title, what it says about the project (a count, the branch), the
 * switch of a merged panel (History | Checkpoints, Memory | Learning, Evals | Reviews) and a close button.
 */
export default function RailPanelHead({
  location,
  extras,
  onView,
  onClose,
}: {
  location: RailLocation;
  extras?: ReactNode;
  onView: (view: RailView) => void;
  onClose: () => void;
}) {
  const { title } = RAIL_ITEMS[location.panel];
  const views = viewsOf(location);
  return (
    <div className="pp-head">
      <h2>{title}</h2>
      {extras}
      {views && location.view !== null && (
        <Segmented
          label={RAIL_ITEMS[location.panel].label}
          value={location.view}
          onChange={onView}
          options={views.map((view) => ({ value: view, label: RAIL_VIEW_LABELS[view] }))}
        />
      )}
      <button type="button" className="icon-button pp-close" aria-label={`Close ${title}`} onClick={onClose}>
        <CloseIcon size={15} />
      </button>
    </div>
  );
}
