import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type MergeState = components["schemas"]["MergeStateOut"];

const POLL_MS = 3000;

export interface Merge {
  /** The merge as the server offers it; null until read. */
  state: MergeState | null;
  error: string | null;
  busy: boolean;
  /** Start the merge with the commands the user was shown. */
  merge: () => Promise<void>;
}

/** A card's merge into its version: read, started with the shown commands, and followed while it runs. */
export function useMerge(projectId: number, cardId: number, onFinished: () => Promise<void>): Merge {
  const [state, setState] = useState<MergeState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const finished = useRef(onFinished);
  finished.current = onFinished;
  const wasRunning = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async (): Promise<void> => {
    const { data, error: failed } = await api.GET("/api/v1/projects/{project_id}/board/cards/{card_id}/merge", {
      params: { path: { project_id: projectId, card_id: cardId } },
    });
    if (!mounted.current) return;
    if (!data) {
      setError(errorMessage(failed, "The merge state could not be read."));
      return;
    }
    setError(null);
    setState(data);
  }, [projectId, cardId]);

  useEffect(() => {
    void load();
  }, [load]);

  const running = state?.running === true || state?.last?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, load]);

  // The card moves when a merge ends, so the board reads it again once.
  useEffect(() => {
    if (running) {
      wasRunning.current = true;
    } else if (wasRunning.current) {
      wasRunning.current = false;
      void finished.current();
    }
  }, [running]);

  async function merge(): Promise<void> {
    if (!state) return;
    setBusy(true);
    const { data, error: failed } = await api.POST("/api/v1/projects/{project_id}/board/cards/{card_id}/merge", {
      params: { path: { project_id: projectId, card_id: cardId } },
      body: { commands: state.version_commands },
    });
    if (!mounted.current) return;
    setBusy(false);
    if (data) {
      setError(null);
      setState(data);
    } else {
      setError(errorMessage(failed, "The merge could not be started."));
    }
  }

  return { state, error, busy, merge };
}
