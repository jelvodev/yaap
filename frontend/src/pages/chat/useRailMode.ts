import { useCallback, useEffect, useState } from "react";

import { RAIL_NARROW_QUERY, type RailMode, railMode } from "./railItems";

/** Whether the window matches a media query, following it as the window is resized. */
function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    // Some browsers (and device emulation) resize without a change event on the list; a resize catches those.
    list.addEventListener("change", update);
    window.addEventListener("resize", update);
    return () => {
      list.removeEventListener("change", update);
      window.removeEventListener("resize", update);
    };
  }, [query]);
  return matches;
}

export interface RailModeState {
  mode: RailMode;
  /** The folded rail's button: show the rail as a drawer over the chat. */
  unfold: () => void;
  /** Fold the drawer away again. */
  fold: () => void;
}

/** Whether the rail stands beside the chat, or folds into a button with a drawer on a narrow window. */
export function useRailMode(panelOpen: boolean): RailModeState {
  const narrow = useMediaQuery(RAIL_NARROW_QUERY);
  const [unfolded, setUnfolded] = useState(false);
  // A window that becomes wide and narrow again starts folded.
  useEffect(() => setUnfolded(false), [narrow]);
  const unfold = useCallback(() => setUnfolded(true), []);
  const fold = useCallback(() => setUnfolded(false), []);
  return { mode: railMode(narrow, unfolded, panelOpen), unfold, fold };
}
