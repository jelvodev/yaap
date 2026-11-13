import { useCallback, useEffect, useState } from "react";

import { type Setting, api, errorMessage } from "../../api/client";

const KEY = "board_parallel_builds";

export interface BoardParallel {
  /** The setting as the project sees it; null until read. */
  setting: Setting | null;
  error: string | null;
  busy: boolean;
  set: (value: string) => Promise<void>;
}

/** The project's `board_parallel_builds` setting: how many approved plans build at once. */
export function useBoardParallel(projectId: number): BoardParallel {
  const [setting, setSetting] = useState<Setting | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const { data, error: failed } = await api.GET("/api/v1/projects/{project_id}/settings", {
      params: { path: { project_id: projectId } },
    });
    if (failed || !data) {
      setError(errorMessage(failed, "The setting could not be read."));
      return;
    }
    setSetting(data.find((item) => item.key === KEY) ?? null);
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function set(value: string): Promise<void> {
    setBusy(true);
    setError(null);
    const { data, error: failed } = await api.PUT("/api/v1/projects/{project_id}/settings/{key}", {
      params: { path: { project_id: projectId, key: KEY } },
      body: { value },
    });
    setBusy(false);
    if (failed || !data) {
      setError(errorMessage(failed, "The setting could not be saved."));
      return;
    }
    setSetting(data);
  }

  return { setting, error, busy, set };
}
