import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type CurrentStateOut = components["schemas"]["CurrentStateOut"];

export interface CurrentState {
  /** The stored block, or null while it is not read (or the project is unknown). */
  state: CurrentStateOut | null;
  loading: boolean;
  /** Why reading failed. */
  error: string | null;
  /** Store a new text; resolves to the error message, or null when it was saved. */
  save: (text: string) => Promise<string | null>;
  reload: () => Promise<void>;
}

/** The "Current state" block of a project's memory: read when the project changes, written with `save`. */
export function useCurrentState(projectId: number | null): CurrentState {
  const [state, setState] = useState<CurrentStateOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The project the latest request is for; an answer for another one arrives too late and is dropped.
  const wanted = useRef(projectId);
  wanted.current = projectId;

  const reload = useCallback(async () => {
    if (projectId === null) return;
    setLoading(true);
    const { data, error: failure } = await api.GET("/api/v1/projects/{project_id}/current-state", {
      params: { path: { project_id: projectId } },
    });
    if (wanted.current !== projectId) return;
    setLoading(false);
    if (data) {
      setState(data);
      setError(null);
    } else {
      setError(errorMessage(failure, "The current state could not be read."));
    }
  }, [projectId]);

  useEffect(() => {
    setState(null);
    setError(null);
    void reload();
  }, [reload]);

  const save = useCallback(
    async (text: string): Promise<string | null> => {
      if (projectId === null) return "No project is open.";
      const { data, error: failure } = await api.PUT("/api/v1/projects/{project_id}/current-state", {
        params: { path: { project_id: projectId } },
        body: { text },
      });
      if (!data) return errorMessage(failure, "The current state could not be saved.");
      if (wanted.current === projectId) setState(data);
      return null;
    },
    [projectId],
  );

  return { state, loading, error, save, reload };
}
