import { useEffect, useState } from "react";

import { type Project, api } from "../../api/client";
import type { components } from "../../api/schema";

export type Preset = components["schemas"]["PresetOut"];

/** The preset the project's cards run on: the one it names, else the environment's default; null until known. */
export function useCardModels(project: Project | null): Preset | null {
  const [preset, setPreset] = useState<Preset | null>(null);
  const environmentId = project?.environment_id ?? null;
  const wanted = project?.default_preset ?? null;

  useEffect(() => {
    setPreset(null);
    if (environmentId === null) return;
    let current = true;
    void api
      .GET("/api/v1/environments/{environment_id}/presets", { params: { path: { environment_id: environmentId } } })
      .then(({ data }) => {
        if (!current) return;
        const presets = data ?? [];
        setPreset(presets.find((item) => item.name === wanted) ?? presets.find((item) => item.is_default) ?? null);
      });
    return () => {
      current = false;
    };
  }, [environmentId, wanted]);

  return preset;
}
