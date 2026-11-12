import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type BackgroundTasks = components["schemas"]["BackgroundTasksOut"];
export type BackgroundTask = components["schemas"]["BackgroundTaskOut"];
export type ScheduledWork = components["schemas"]["ScheduledWorkOut"];

/** The background tasks of an environment; `reload` fetches them again, for instance after a switch was saved. */
export function useBackgroundTasks(environmentId: number | null): {
  tasks: BackgroundTasks | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
} {
  const [tasks, setTasks] = useState<BackgroundTasks | null>(null);
  const [loading, setLoading] = useState(environmentId !== null);
  const [error, setError] = useState<string | null>(null);
  // Only the newest request may set the state, so a slow answer for another environment never shows up here.
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const request = ++latest.current;
    if (environmentId === null) {
      setTasks(null);
      setLoading(false);
      return;
    }
    const { data, error: requestError } = await api.GET("/api/v1/environments/{environment_id}/background-tasks", {
      params: { path: { environment_id: environmentId } },
    });
    if (request !== latest.current) return;
    if (requestError || !data) {
      setError(errorMessage(requestError, "Background tasks could not be loaded."));
    } else {
      setError(null);
      setTasks(data);
    }
    setLoading(false);
  }, [environmentId]);

  useEffect(() => {
    setLoading(environmentId !== null);
    void reload();
  }, [environmentId, reload]);

  return { tasks, loading, error, reload };
}
