import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import type { components } from "../../api/schema";

type Process = components["schemas"]["ProcessOut"];

/** How long the panel waits after a file change before reloading, so a burst of edits reloads once. */
const RELOAD_DELAY_MS = 400;
const POLL_MS = 5000;

/** Who opens the preview: the project rail (`useRailSelection`) holds whether it is open. */
export interface PreviewPanelOptions {
  /** Whether the preview panel beside the chat is open. */
  open: boolean;
  /** Close the preview panel (its close button, and another project). */
  close: () => void;
  /** Keep reading the running processes while the panel is closed, for the rail's dot. */
  watch: boolean;
}

export interface PreviewPanelState {
  /** Whether the preview panel beside the chat is open. */
  open: boolean;
  close: () => void;
  /** The project's running processes that reported a port. */
  processes: Process[];
  processId: number | null;
  choose: (processId: number) => void;
  /** The address the frame loads: a fresh open link of the preview, or null before one is made. */
  src: string | null;
  /** Changes with every reload, so the frame loads `src` again. */
  frameKey: number;
  reload: () => void;
  /** The agent changed a project file: reload shortly, when the panel shows a preview. */
  notifyFileChanged: () => void;
  /** The number of the last screenshot of the agent's browser, shown under the frame. */
  shot: number | null;
  takingShot: boolean;
  screenshot: () => void;
  error: string | null;
}

function failure(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "detail" in error && typeof error.detail === "string") return error.detail;
  return fallback;
}

/** The project's processes with a port, polled while the panel is open or watched. */
function usePortProcesses(projectId: number | null, open: boolean): Process[] {
  const [processes, setProcesses] = useState<Process[]>([]);
  useEffect(() => {
    if (!open || projectId === null) {
      setProcesses([]);
      return;
    }
    let current = true;
    const load = async () => {
      const { data } = await api.GET("/api/v1/projects/{project_id}/processes", {
        params: { path: { project_id: projectId } },
      });
      if (current) setProcesses((data ?? []).filter((item) => item.status === "running" && item.port !== null));
    };
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [projectId, open]);
  return processes;
}

/**
 * The preview panel: which process it shows, a fresh open link per load (a link works once and for a
 * few minutes; the preview's own cookie carries on from there), a reload after each file change of the agent, and a
 * screenshot through the agent's browser.
 */
export function usePreviewPanel(
  projectId: number | null,
  conversationId: number | null,
  { open, close, watch }: PreviewPanelOptions,
): PreviewPanelState {
  const [processId, setProcessId] = useState<number | null>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [frameKey, setFrameKey] = useState(0);
  const [shot, setShot] = useState<number | null>(null);
  const [takingShot, setTakingShot] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reloadTimer = useRef<number | null>(null);
  const processes = usePortProcesses(projectId, open || watch);
  const closeRef = useRef(close);
  closeRef.current = close;

  // Show a running process: keep the chosen one while it runs, else the newest with a port.
  useEffect(() => {
    if (processId !== null && processes.some((item) => item.id === processId)) return;
    setProcessId(processes[0]?.id ?? null);
  }, [processes, processId]);

  useEffect(() => {
    closeRef.current();
    setProcessId(null);
    setShot(null);
  }, [projectId]);

  const load = useCallback(async () => {
    if (projectId === null || processId === null) {
      setSrc(null);
      return;
    }
    const { data, error: refused } = await api.POST("/api/v1/projects/{project_id}/processes/{process_id}/preview", {
      params: { path: { project_id: projectId, process_id: processId } },
    });
    if (!data) {
      setError(failure(refused, "The preview could not be opened."));
      return;
    }
    setError(null);
    setSrc(data.url);
    setFrameKey((key) => key + 1);
  }, [projectId, processId]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  useEffect(
    () => () => {
      if (reloadTimer.current !== null) window.clearTimeout(reloadTimer.current);
    },
    [],
  );

  const notifyFileChanged = useCallback(() => {
    if (!open || processId === null) return;
    if (reloadTimer.current !== null) window.clearTimeout(reloadTimer.current);
    reloadTimer.current = window.setTimeout(() => {
      reloadTimer.current = null;
      void load();
    }, RELOAD_DELAY_MS);
  }, [open, processId, load]);

  const screenshot = useCallback(() => {
    if (conversationId === null || processId === null) return;
    setTakingShot(true);
    void api
      .POST("/api/v1/conversations/{conversation_id}/preview/screenshot", {
        params: { path: { conversation_id: conversationId } },
        body: { process_id: processId, path: "/" },
      })
      .then(({ data, error: refused }) => {
        if (data) {
          setShot(data.number);
          setError(null);
        } else {
          setError(failure(refused, "The screenshot could not be taken."));
        }
      })
      .finally(() => setTakingShot(false));
  }, [conversationId, processId]);

  return {
    open,
    close,
    processes,
    processId,
    choose: setProcessId,
    src: processId === null ? null : src,
    frameKey,
    reload: () => void load(),
    notifyFileChanged,
    shot,
    takingShot,
    screenshot,
    error,
  };
}
