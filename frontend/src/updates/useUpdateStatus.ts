import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";

export type UpdateStatus = components["schemas"]["UpdateStatusOut"];

export interface UpdateStatusState {
  status: UpdateStatus | null;
  /** A check by hand is running. */
  checking: boolean;
  /** The check by hand could not be asked for at all; a check that GitHub refused is in `status.error`. */
  problem: string | null;
  checkNow: () => Promise<void>;
}

/** The result of the last update check, and checking by hand. */
export function useUpdateStatus(): UpdateStatusState {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    void api.GET("/api/v1/updates").then(({ data }) => {
      if (data) setStatus(data);
    });
  }, []);

  const checkNow = useCallback(async () => {
    setChecking(true);
    setProblem(null);
    const { data, error } = await api.POST("/api/v1/updates/check");
    setChecking(false);
    if (data) setStatus(data);
    else setProblem(errorMessage(error, "The check could not be started."));
  }, []);

  return { status, checking, problem, checkNow };
}
