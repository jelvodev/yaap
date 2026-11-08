import { useCallback, useEffect, useState } from "react";

import { type ModelInfo, type Preset, type Project, api } from "../../api/client";
import type { components } from "../../api/schema";

export type TargetInfo = components["schemas"]["TargetOut"];

/** What the chat of an environment offers besides its models. */
export interface ChatBasics {
  /** True once projects and presets have arrived. */
  loaded: boolean;
  presets: Preset[];
  projects: Project[];
  /** Computers outside YAAP a conversation outside a project can work on. */
  targets: TargetInfo[];
  addProject: (project: Project) => void;
  replaceProject: (project: Project) => void;
  removeProject: (projectId: number) => void;
}

export interface ChatCatalog extends ChatBasics {
  /** True once models, projects and presets have arrived. */
  loaded: boolean;
  /** Every model, marked for the environment and the project of the chat (a key, the model policy). */
  models: ModelInfo[];
}

/** What the chat of an environment offers: its presets, projects and targets. */
export function useChatCatalog(environmentId: number): ChatBasics {
  const [loaded, setLoaded] = useState(false);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [targets, setTargets] = useState<TargetInfo[]>([]);

  useEffect(() => {
    let current = true;
    const path = { params: { path: { environment_id: environmentId } } };
    void Promise.all([
      api.GET("/api/v1/environments/{environment_id}/projects", path),
      api.GET("/api/v1/environments/{environment_id}/presets", path),
    ]).then(([projectsResult, presetsResult]) => {
      if (!current) return;
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

  return { loaded, presets, projects, targets, addProject, replaceProject, removeProject };
}

/**
 * The catalog with the models of the environment, marked for the chat's project: a project's model policy can forbid
 * models the environment allows. A separate hook, since the project follows from the catalog's projects.
 */
export function useChatModels(basics: ChatBasics, environmentId: number, projectId: number | null): ChatCatalog {
  // Another project's list stays until this one's arrives, so the pickers do not empty in between.
  const [models, setModels] = useState<ModelInfo[] | null>(null);

  useEffect(() => {
    let current = true;
    const query = projectId === null ? {} : { project_id: projectId };
    void api
      .GET("/api/v1/environments/{environment_id}/models", {
        params: { path: { environment_id: environmentId }, query },
      })
      .then(({ data }) => current && setModels(data ?? []));
    return () => {
      current = false;
    };
  }, [environmentId, projectId]);

  return { ...basics, loaded: basics.loaded && models !== null, models: models ?? [] };
}
