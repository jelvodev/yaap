/**
 * The project rail: which panel beside the chat is open, on which view, and the counts on the rail's icons.
 *
 * The contract the chat header, the rail and the Changes panel build on:
 *
 * - `RailPanel` (`./railPanels`) names the panels, top to bottom: changes, files, branches (and pull requests),
 *   history (`view` "history" | "checkpoints"), preview, memory ("memory" | "learning"), knowledge, tasks, evals
 *   ("evals" | "reviews") and settings (Project settings). `RailLocation` is `{ panel, view }`; `view` is null for a
 *   panel without a switch.
 * - `rail.open(panel, view?)` opens a panel (on its first view when none is given); `rail.toggle(panel, view?)` is a
 *   rail icon's click: a second click on the active panel closes it; `rail.close()` closes whatever is open, and
 *   `rail.close(panel)` only that panel. `rail.openLink(value)` opens what a link or an old tab value names
 *   (`?panel=checkpoints`, `?panel=project`); `linkFromLocation` gives the stable value back for an address.
 * - `rail.active`, `rail.panel` and `rail.view` say what is open (null while closed).
 * - `rail.counts`: `changedFiles` (uncommitted changes), `previewRunning` (a process with a port runs) and
 *   `openProposals` (learning proposals waiting). They come from the state the panels themselves show, never from
 *   a second request: `rail.git` (the project's git state, also behind the Changes panel and the header chip),
 *   `rail.preview` (the preview panel's processes) and `rail.learning` (the Learning view's proposals).
 * - The counts are read while the panel that shows them is open. With `watchCounts` (set once the rail is always on
 *   screen) they are read while the panels are closed too: the git state on every `refreshKey`, the processes
 *   every few seconds, the proposals when the project changes. `WATCH_RAIL_COUNTS` in `ChatPage` switches it on;
 *   the header chip needs it as much as the rail does.
 * - One panel is open at a time: the preview is a rail panel, so opening it closes the project panel and back.
 * - `rail.focusCheckpoint` and `rail.focusReview` open History on Checkpoints and Evals on Reviews at one item.
 *
 * It is built in two steps because the conversation's session sits between them: `useRailSelection` (what is open)
 * comes before the session, which opens Evals after /eval; `useProjectRail` adds the counts once the session says
 * whether a reply runs.
 */
import { useCallback, useMemo, useState } from "react";

import type { Project } from "../../api/client";
import type { CheckpointFocus } from "../../components/project/CheckpointsTab";
import { type LearningProposals, useLearningProposals } from "../../components/project/useLearning";
import { type ProjectRepository, useProjectRepository } from "../../components/project/useProjectRepository";
import {
  type PanelTab,
  type RailLocation,
  type RailPanel,
  type RailView,
  type RailViewOf,
  locationFromLink,
  panelTabOf,
  railLocation,
} from "./railPanels";
import type { PreviewPanelState } from "./usePreviewPanel";

export interface RailSelection {
  /** The open panel with its view, or null while every panel is closed. */
  active: RailLocation | null;
  panel: RailPanel | null;
  view: RailView | null;
  /** The project panel's tab for the open panel; null while it is closed or the preview is open. */
  projectTab: PanelTab | null;
  open: <P extends RailPanel>(panel: P, view?: RailViewOf<P>) => void;
  /** Open what a link or an old tab value names; false when it names nothing. */
  openLink: (value: string) => boolean;
  /** A rail icon's click: opens the panel, or closes it when it is the active one. */
  toggle: <P extends RailPanel>(panel: P, view?: RailViewOf<P>) => void;
  /** Close the open panel; with a panel, only when that one is open. */
  close: (panel?: RailPanel) => void;
  /** The checkpoint History shows on Checkpoints. */
  checkpointFocus: CheckpointFocus | null;
  focusCheckpoint: (checkpointId: number, view: CheckpointFocus["view"]) => void;
  /** The review Evals shows on Reviews. */
  reviewFocus: number | null;
  focusReview: (reviewId: number) => void;
}

/** Which panel beside the chat is open, and the checkpoint or review it was asked to show. */
export function useRailSelection(): RailSelection {
  const [active, setActive] = useState<RailLocation | null>(null);
  const [checkpointFocus, setCheckpointFocus] = useState<CheckpointFocus | null>(null);
  const [reviewFocus, setReviewFocus] = useState<number | null>(null);

  const open = useCallback(<P extends RailPanel>(panel: P, view?: RailViewOf<P>) => {
    setActive(railLocation(panel, view ?? null));
  }, []);
  const openLink = useCallback((value: string) => {
    const location = locationFromLink(value);
    if (location) setActive(location);
    return location !== null;
  }, []);
  const toggle = useCallback(<P extends RailPanel>(panel: P, view?: RailViewOf<P>) => {
    setActive((current) => (current?.panel === panel ? null : railLocation(panel, view ?? null)));
  }, []);
  const close = useCallback((panel?: RailPanel) => {
    setActive((current) => (panel === undefined || current?.panel === panel ? null : current));
  }, []);
  const focusCheckpoint = useCallback((checkpointId: number, view: CheckpointFocus["view"]) => {
    setCheckpointFocus({ id: checkpointId, view });
    setActive(railLocation("history", "checkpoints"));
  }, []);
  const focusReview = useCallback((reviewId: number) => {
    setReviewFocus(reviewId);
    setActive(railLocation("evals", "reviews"));
  }, []);

  return {
    active,
    panel: active?.panel ?? null,
    view: active?.view ?? null,
    projectTab: panelTabOf(active),
    open,
    openLink,
    toggle,
    close,
    checkpointFocus,
    focusCheckpoint,
    reviewFocus,
    focusReview,
  };
}

/** The counts on the rail's icons. */
export interface RailCounts {
  /** Files with uncommitted changes in the project. */
  changedFiles: number;
  /** A process of the project with a port runs, so the preview has something to show. */
  previewRunning: boolean;
  /** Learning proposals waiting for a decision. */
  openProposals: number;
}

export interface ProjectRailSources {
  /** The project the panels show: the open conversation's, or the one chosen in the sidebar. */
  project: Project | null;
  /** Changes when a reply may have changed files, so the git state is read again. */
  refreshKey: unknown;
  /** The preview panel, opened and closed through the selection. */
  preview: PreviewPanelState;
  /** Read the counts while their panels are closed too (once the rail is always on screen). */
  watchCounts: boolean;
}

export interface ProjectRail extends RailSelection {
  counts: RailCounts;
  git: ProjectRepository;
  preview: PreviewPanelState;
  learning: LearningProposals;
}

/** The rail's selection with the state behind its counts, read once and shared with the panels that show it. */
export function useProjectRail(selection: RailSelection, sources: ProjectRailSources): ProjectRail {
  const { project, refreshKey, preview, watchCounts } = sources;
  // The project panel reads the git state whenever it is open, on any tab, as it did on its own.
  const git = useProjectRepository(project, refreshKey, watchCounts || selection.projectTab !== null);
  const learning = useLearningProposals(project?.id ?? null, watchCounts || selection.view === "learning");
  const changedFiles = git.repository?.changes.length ?? 0;
  const previewRunning = preview.processes.length > 0;
  const openProposals = learning.proposals?.length ?? 0;
  const counts = useMemo(
    () => ({ changedFiles, previewRunning, openProposals }),
    [changedFiles, previewRunning, openProposals],
  );
  return { ...selection, counts, git, preview, learning };
}
