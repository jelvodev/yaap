import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";

export type FirstRun = components["schemas"]["FirstRunOut"];
export type FirstRunStep = keyof FirstRun["steps"];
export type StepState = FirstRun["steps"][FirstRunStep];

export const WELCOME_PATH = "/welcome";

type Answer = { data?: FirstRun; error?: unknown };

/** The first start: per step whether it is done, skipped or still to do, and starting, skipping and finishing it. */
export function useFirstRun({ load = true }: { load?: boolean } = {}) {
  const [status, setStatus] = useState<FirstRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(({ data, error: failure }: Answer): FirstRun | null => {
    if (!data) {
      setError(errorMessage(failure, "The first start could not be loaded."));
      return null;
    }
    setError(null);
    setStatus(data);
    return data;
  }, []);

  const reload = useCallback(async () => apply(await api.GET("/api/v1/first-run")), [apply]);
  const start = useCallback(async () => apply(await api.POST("/api/v1/first-run/start")), [apply]);
  const finish = useCallback(async () => apply(await api.POST("/api/v1/first-run/finish")), [apply]);
  const restart = useCallback(async () => apply(await api.POST("/api/v1/first-run/restart")), [apply]);
  const skip = useCallback(
    async (step: FirstRunStep) =>
      apply(await api.POST("/api/v1/first-run/skip/{step}", { params: { path: { step } } })),
    [apply],
  );

  useEffect(() => {
    if (load) void reload();
  }, [load, reload]);

  return { status, error, reload, start, skip, finish, restart };
}

export type FirstRunControl = ReturnType<typeof useFirstRun>;

/**
 * After sign-in, a new installation is sent to the first start once per visit. Leaving it with Finish later keeps
 * the rest of the visit free; it comes back on the next one until it is finished.
 */
export function useFirstRunRedirect(): void {
  const { status } = useFirstRun();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const checked = useRef(false);

  useEffect(() => {
    if (!status || checked.current) return;
    checked.current = true;
    if (status.show && pathname !== WELCOME_PATH) navigate(WELCOME_PATH);
  }, [status, pathname, navigate]);
}
