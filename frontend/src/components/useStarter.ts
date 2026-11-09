import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";
import type { LevelQuery } from "./usePackageReviews";

export type Starter = components["schemas"]["StarterOut"];
export type StarterPack = components["schemas"]["StarterPackOut"];

/** YAAP's starter as one level sees it: the packs at the ref of the running version and which of them it has. */
export function useStarter(query: LevelQuery, levelKey: string) {
  const [starter, setStarter] = useState<Starter | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Only the newest request may set the state, so a slow answer for another level never shows up here.
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const request = ++latest.current;
    const { data, error: failure } = await api.GET("/api/v1/blueprint/starter", { params: { query } });
    if (request !== latest.current) return;
    setStarter(data ?? null);
    setError(data ? null : errorMessage(failure, "YAAP's starter could not be read from GitHub."));
    // The level's key stands for its query, which is a new object on every render.
  }, [levelKey]);

  useEffect(() => {
    setStarter(null);
    void reload();
  }, [reload]);

  return { starter, error, reload };
}
