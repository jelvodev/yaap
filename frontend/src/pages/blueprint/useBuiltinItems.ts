import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { BuiltinItem } from "./builtinLogic";

export interface BuiltinItems {
  items: BuiltinItem[] | null;
  error: string | null;
  reload: () => Promise<void>;
}

/** The built-in agents and skills as seen from one environment (its layers decide which ones are overridden). */
export function useBuiltinItems(environmentId: number | null): BuiltinItems {
  const [items, setItems] = useState<BuiltinItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (environmentId === null) return;
    const { data, error: failure } = await api.GET("/api/v1/environments/{environment_id}/blueprint/builtin", {
      params: { path: { environment_id: environmentId } },
    });
    if (failure) setError(errorMessage(failure));
    else {
      setError(null);
      setItems(data?.items ?? []);
    }
  }, [environmentId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { items, error, reload };
}
