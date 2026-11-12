import { useCallback, useEffect, useState } from "react";

import { type Project, type Setting, api, errorMessage } from "../../api/client";

const KEY = "project_board";

export interface BoardSwitch {
  /** The setting as the project sees it; null until read. */
  setting: Setting | null;
  enabled: boolean;
  /** Whether the project sets it itself, or inherits it. */
  own: boolean;
  error: string | null;
  busy: boolean;
  /** true or false sets it for the project; null goes back to the inherited value. */
  set: (value: boolean | null) => Promise<void>;
}

/**
 * The project's board switch (the `project_board` setting at project level). After a change the project is read
 * again, so its `board_enabled` (which the sidebar uses) follows.
 */
export function useBoardSwitch(projectId: number, onProjectChanged?: (project: Project) => void): BoardSwitch {
  const [setting, setSetting] = useState<Setting | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const path = { project_id: projectId, key: KEY };

  const load = useCallback(async () => {
    const { data, error: failed } = await api.GET("/api/v1/projects/{project_id}/settings", {
      params: { path: { project_id: projectId } },
    });
    if (failed || !data) {
      setError(errorMessage(failed, "The board setting could not be read."));
      return;
    }
    setSetting(data.find((item) => item.key === KEY) ?? null);
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function set(value: boolean | null) {
    setBusy(true);
    setError(null);
    const { data, error: failed } =
      value === null
        ? await api.DELETE("/api/v1/projects/{project_id}/settings/{key}", { params: { path } })
        : await api.PUT("/api/v1/projects/{project_id}/settings/{key}", { params: { path }, body: { value } });
    if (failed || !data) {
      setBusy(false);
      setError(errorMessage(failed, "The board setting could not be saved."));
      return;
    }
    setSetting(data);
    const project = await api.GET("/api/v1/projects/{project_id}", { params: { path: { project_id: projectId } } });
    setBusy(false);
    if (project.data) onProjectChanged?.(project.data);
  }

  const own = setting !== null && setting.level_value !== null && setting.level_value !== undefined;
  return { setting, enabled: setting?.value === true, own, error, busy, set };
}
