import { useCallback, useEffect, useState } from "react";

import { type GitRepository, api, errorMessage } from "../../api/client";
import type { Level } from "../../components/workbench/LevelPicker";
import { useEnvironment } from "../../environment/EnvironmentProvider";

export interface LayerRepository {
  environmentId: number;
  repository: GitRepository | undefined;
}

export interface GitSync {
  reloadRepositories: () => Promise<void>;
  /** The repository of a level's layer, for sync state and history. */
  layerRepository: (entry: Level) => LayerRepository | null;
  remoteDraft: string | null;
  setRemoteDraft: (value: string | null) => void;
  syncBusy: boolean;
  syncError: string | null;
  syncAction: (action: "push" | "remote") => Promise<void>;
}

/** Git state of the Blueprint layers (per environment) and the push / set-remote actions for the open level. */
export function useGitSync(level: Level, environmentId: number | null): GitSync {
  const { environments } = useEnvironment();
  const [repositories, setRepositories] = useState<Record<number, GitRepository[]>>({});
  const [remoteDraft, setRemoteDraft] = useState<string | null>(null);
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);

  const reloadRepositories = useCallback(async () => {
    const entries = await Promise.all(
      environments.map(async (entry) => {
        const { data } = await api.GET("/api/v1/environments/{environment_id}/git/repositories", {
          params: { path: { environment_id: entry.id } },
        });
        return [entry.id, data ?? []] as const;
      }),
    );
    setRepositories(Object.fromEntries(entries));
  }, [environments]);

  useEffect(() => {
    void reloadRepositories();
  }, [reloadRepositories]);

  function layerRepository(entry: Level): LayerRepository | null {
    if (entry.kind === "global") {
      if (environmentId === null) return null;
      return { environmentId, repository: repositories[environmentId]?.find((repo) => repo.id === "blueprint-global") };
    }
    if (entry.kind === "environment") {
      return {
        environmentId: entry.environmentId,
        repository: repositories[entry.environmentId]?.find((repo) => repo.id === "blueprint-environment"),
      };
    }
    return {
      environmentId: entry.environmentId,
      repository: repositories[entry.environmentId]?.find((repo) => repo.id === `project-${entry.projectId}`),
    };
  }

  async function syncAction(action: "push" | "remote") {
    const layer = layerRepository(level);
    if (!layer?.repository) return;
    setSyncBusy(true);
    setSyncError(null);
    const repoPath = { environment_id: layer.environmentId, repository_id: layer.repository.id };
    const { error } =
      action === "push"
        ? await api.POST("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/push", { params: { path: repoPath } })
        : await api.PUT("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/remote", {
            params: { path: repoPath },
            body: { url: remoteDraft ?? "" },
          });
    setSyncBusy(false);
    if (error) setSyncError(errorMessage(error));
    else setRemoteDraft(null);
    await reloadRepositories();
  }

  return { reloadRepositories, layerRepository, remoteDraft, setRemoteDraft, syncBusy, syncError, syncAction };
}
