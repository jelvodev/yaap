import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import type { components } from "../../api/schema";

export type Run = components["schemas"]["RunOut"];

export const RUNS_PAGE = 50;
// The most the runs list sends at once; a refresh of more loaded runs than this keeps the rest as they were.
const MAX_PAGE = 200;

/**
 * The runs of one agent (or every reply's run without one), newest first, a page at a time. Sub-runs are not listed
 * beside their run: they are in its trace. The loaded runs are refreshed every few seconds while one of them runs.
 */
export function useAgentRuns(environmentId: number, agent: string | undefined) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const fetchPage = useCallback(
    async (before: number | null, limit: number) => {
      const query = { sub_runs: false, limit, ...(agent ? { agent } : {}), ...(before ? { before } : {}) };
      const { data } = await api.GET("/api/v1/environments/{environment_id}/runs", {
        params: { path: { environment_id: environmentId }, query },
      });
      return data ?? [];
    },
    [environmentId, agent],
  );

  useEffect(() => {
    let current = true;
    setRuns(null);
    void fetchPage(null, RUNS_PAGE).then((page) => {
      if (!current) return;
      setRuns(page);
      setMore(page.length === RUNS_PAGE);
    });
    return () => {
      current = false;
    };
  }, [fetchPage]);

  /** The newest runs again, as many as are loaded (up to a page's maximum); older loaded runs stay as they were. */
  const reload = useCallback(
    async (loaded: number) => {
      const page = await fetchPage(null, Math.min(MAX_PAGE, Math.max(RUNS_PAGE, loaded)));
      const oldest = page.at(-1)?.id;
      setRuns((known) => [...page, ...(known ?? []).filter((run) => oldest !== undefined && run.id < oldest)]);
    },
    [fetchPage],
  );

  const running = runs?.some((run) => run.status === "running") ?? false;
  const loaded = runs?.length ?? 0;
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void reload(loaded), 4000);
    return () => window.clearInterval(timer);
  }, [running, loaded, reload]);

  const loadMore = useCallback(async () => {
    const last = runs?.at(-1);
    if (!last) return;
    setLoadingMore(true);
    const page = await fetchPage(last.id, RUNS_PAGE);
    setRuns((known) => [...(known ?? []), ...page.filter((run) => run.id < last.id)]);
    setMore(page.length === RUNS_PAGE);
    setLoadingMore(false);
  }, [runs, fetchPage]);

  return { runs, more, loadingMore, loadMore };
}
