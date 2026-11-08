import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import type { components } from "../../api/schema";

export type UsageSummary = components["schemas"]["UsageSummaryOut"];

/** This month's usage and budgets of the environment, for the card at the bottom of the sidebar. */
export function useUsage(environmentId: number): { usage: UsageSummary | null; reload: () => Promise<void> } {
  const [usage, setUsage] = useState<UsageSummary | null>(null);

  const reload = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/usage", {
      params: { path: { environment_id: environmentId } },
    });
    if (data) setUsage(data);
  }, [environmentId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { usage, reload };
}
