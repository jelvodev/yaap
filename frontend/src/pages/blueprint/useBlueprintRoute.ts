import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import type { Level } from "../../components/workbench/LevelPicker";
import { useEnvironment } from "../../environment/EnvironmentProvider";
import { type Filter, kindOfPath, levelFromParams } from "./blueprintRoute";
import type { ItemKind } from "./model";

export interface BlueprintRoute {
  params: URLSearchParams;
  setParams: ReturnType<typeof useSearchParams>[1];
  level: Level;
  path: string | null;
  kind: ItemKind;
  view: "list" | "matrix";
  filter: Filter;
  /** The environment whose layers, projects and agents surround the level; Global is seen from the active one. */
  environmentId: number | null;
  update: (patch: Record<string, string | null>, replace?: boolean) => void;
}

/** The Blueprint page's navigation state: level, item path, kind, view and filter live in the URL. */
export function useBlueprintRoute(): BlueprintRoute {
  const { active } = useEnvironment();
  const [params, setParams] = useSearchParams();

  const level: Level = useMemo(
    () => levelFromParams(params) ?? (active ? { kind: "environment", environmentId: active.id } : { kind: "global" }),
    [params, active],
  );
  const path = params.get("path");
  const kind: ItemKind = (params.get("kind") as ItemKind | null) ?? (path ? kindOfPath(path) : null) ?? "standard";
  const view = params.get("view") === "matrix" ? "matrix" : "list";
  const filter = (params.get("filter") as Filter | null) ?? "all";
  const environmentId = level.kind === "global" ? (active?.id ?? null) : level.environmentId;

  function update(patch: Record<string, string | null>, replace = false) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace });
  }

  useEffect(() => {
    // The starter link has done its work once the panel is open; a reload shows the list again.
    if (params.has("add")) update({ add: null }, true);
  }, []);

  return { params, setParams, level, path, kind, view, filter, environmentId, update };
}
