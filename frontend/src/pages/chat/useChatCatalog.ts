import { useCallback, useEffect, useState } from "react";

import { type ModelInfo, type Preset, type Project, api } from "../../api/client";
import type { components } from "../../api/schema";

export type TargetInfo = components["schemas"]["TargetOut"];

export interface ChatCatalog {
  /** True once models, projects and presets have arrived. */
  loaded: boolean;
  models: ModelInfo[];
  presets: Preset[];
  projects: Project[];
  /** Computers outside YAAP a conversation outside a project can work on. */
  targets: TargetInfo[];
  addProject: (project: Project) => void;
  replaceProject: (project: Project) => void;
  removeProject: (projectId: number) => void;
}

/** What the chat of an environment offers: its models, presets, projects and targets. */
export function useChatCatalog(environmentId: number): ChatCatalog {
  const [loaded, setLoaded] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [targets, setTargets] = useState<TargetInfo[]>([]);

  useEffect(() => {
    let current = true;
    const path = { params: { path: { environment_id: environmentId } } };
    void Promise.all([
      api.GET("/api/v1/environments/{environment_id}/models", path),
      api.GET("/api/v1/environments/{environment_id}/projects", path),
      api.GET("/api/v1/environments/{environment_id}/presets", path),
    ]).then(([modelsResult, projectsResult, presetsResult]) => {
      if (!current) return;
      setModels(modelsResult.data ?? []);
      setProjects(projectsResult.data ?? []);
      setPresets(presetsResult.data ?? []);
      setLoaded(true);
    });
    void api
      .GET("/api/v1/environments/{environment_id}/targets", path)
      .then(({ data }) => current && setTargets(data ?? []));
    return () => {
      current = false;
    };
  }, [environmentId]);

  const addProject = useCallback((project: Project) => {
    setProjects((list) => [...list, project].sort((a, b) => a.name.localeCompare(b.name)));
  }, []);
  const replaceProject = useCallback((project: Project) => {
    setProjects((list) => list.map((item) => (item.id === project.id ? project : item)));
  }, []);
  const removeProject = useCallback((projectId: number) => {
    setProjects((list) => list.filter((item) => item.id !== projectId));
  }, []);

  return { loaded, models, presets, projects, targets, addProject, replaceProject, removeProject };
}
