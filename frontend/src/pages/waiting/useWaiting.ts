import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import { LOOKED_EVENT, readLastLooked, writeLastLooked } from "./lastLooked";

export type Waiting = components["schemas"]["WaitingOut"];
export type WaitingItem = components["schemas"]["WaitingItemOut"];
export type WaitingProject = components["schemas"]["WaitingProjectOut"];

/** How often the page and the sidebar's count ask again. */
export const WAITING_POLL_MS = 30_000;

type WaitingResult = { ok: true; data: Waiting } | { ok: false; message: string };

async function fetchWaiting(environmentId: number, since: string | null): Promise<WaitingResult> {
  const { data, error } = await api.GET("/api/v1/waiting", {
    params: { query: { environment_id: environmentId, since: since ?? undefined } },
  });
  if (data) return { ok: true, data };
  return { ok: false, message: errorMessage(error, "Could not read what waits for you.") };
}

/** Calls `load` now and every half minute while mounted. */
function usePolling(load: () => Promise<void>): void {
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), WAITING_POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);
}

/**
 * The overview for the page. Inbox cards count as new against the last look before this page opened; the first answer
 * records a new look, while the page keeps its own moment so the rows it shows do not vanish as it polls.
 */
export function useWaiting(environmentId: number): { waiting: Waiting | null; error: string | null } {
  const [since] = useState(() => readLastLooked(environmentId));
  const [waiting, setWaiting] = useState<Waiting | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recorded, setRecorded] = useState(false);
  const load = useCallback(async () => {
    const result = await fetchWaiting(environmentId, since);
    if (!result.ok) {
      setError(result.message);
      return;
    }
    setWaiting(result.data);
    setError(null);
  }, [environmentId, since]);
  usePolling(load);
  useEffect(() => {
    if (waiting === null || recorded) return;
    writeLastLooked(environmentId, waiting.now);
    setRecorded(true);
  }, [environmentId, waiting, recorded]);
  return { waiting, error };
}

/** The number of rows for the sidebar's entry; null until the first answer or when it fails. */
export function useWaitingCount(environmentId: number): number | null {
  const [count, setCount] = useState<number | null>(null);
  const load = useCallback(async () => {
    const result = await fetchWaiting(environmentId, readLastLooked(environmentId));
    setCount(result.ok ? result.data.items.length : null);
  }, [environmentId]);
  usePolling(load);
  useEffect(() => {
    const again = () => void load();
    window.addEventListener(LOOKED_EVENT, again);
    return () => window.removeEventListener(LOOKED_EVENT, again);
  }, [load]);
  return count;
}
