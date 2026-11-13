import { useCallback, useEffect, useState } from "react";

import { type VersionFilter, filterKey, parseFilterKey } from "./boardLogic";

export type BoardView = "board" | "list";

export interface BoardPrefs {
  filter: VersionFilter;
  setFilter: (filter: VersionFilter) => void;
  view: BoardView;
  setView: (view: BoardView) => void;
  doneOpen: boolean;
  setDoneOpen: (open: boolean) => void;
}

// Storage can be blocked or full; the preferences are a convenience, so a failure means the defaults.
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
    // Not kept; the choice still holds for this visit.
  }
}

function storageKey(projectId: number, name: string): string {
  return `yaap.board.${projectId}.${name}`;
}

/** The version filter, the view and whether Done is unfolded, kept per project in this browser. */
export function useBoardPrefs(projectId: number): BoardPrefs {
  const load = useCallback(
    () => ({
      filter: parseFilterKey(read(storageKey(projectId, "filter"))),
      view: read(storageKey(projectId, "view")) === "list" ? ("list" as const) : ("board" as const),
      doneOpen: read(storageKey(projectId, "done")) === "open",
    }),
    [projectId],
  );
  const [state, setState] = useState(load);
  useEffect(() => setState(load()), [load]);

  const setFilter = useCallback(
    (filter: VersionFilter) => {
      write(storageKey(projectId, "filter"), filterKey(filter));
      setState((current) => ({ ...current, filter }));
    },
    [projectId],
  );
  const setView = useCallback(
    (view: BoardView) => {
      write(storageKey(projectId, "view"), view);
      setState((current) => ({ ...current, view }));
    },
    [projectId],
  );
  const setDoneOpen = useCallback(
    (open: boolean) => {
      write(storageKey(projectId, "done"), open ? "open" : "closed");
      setState((current) => ({ ...current, doneOpen: open }));
    },
    [projectId],
  );
  return { ...state, setFilter, setView, setDoneOpen };
}

const NARROW_QUERY = "(max-width: 900px)";

/** Whether the window is narrow enough for the list layout. */
export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW_QUERY).matches);
  useEffect(() => {
    const query = window.matchMedia(NARROW_QUERY);
    const onChange = () => setNarrow(query.matches);
    onChange();
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return narrow;
}
