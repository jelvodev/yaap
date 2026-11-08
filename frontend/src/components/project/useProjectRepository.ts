import { useCallback, useEffect, useRef, useState } from "react";

import { type GitRepository, type Project, api, errorMessage } from "../../api/client";

export interface ProjectRepository {
  /** The project's git state, or null before it is read. */
  repository: GitRepository | null;
  /** Why the git state could not be read, or the repository's own error. */
  error: string | null;
  reload: () => Promise<void>;
  /** An action returned the new git state (commit, push, a branch switch): show it without reading again. */
  replace: (repository: GitRepository) => void;
}

/**
 * The git state of the project beside the chat, shared by the project panel and the counts on the rail. It is read
 * while `enabled`, again whenever `refreshKey` changes (a reply may have changed files), and from scratch for another
 * project.
 */
export function useProjectRepository(project: Project | null, refreshKey: unknown, enabled: boolean): ProjectRepository {
  const [repository, setRepository] = useState<GitRepository | null>(null);
  const [error, setError] = useState<string | null>(null);
  const environmentId = project?.environment_id ?? null;
  const repositoryId = project ? `project-${project.id}` : null;
  // The repository the latest read is for; an answer for another one arrives too late and is dropped.
  const wanted = useRef(repositoryId);
  wanted.current = repositoryId;

  useEffect(() => {
    setRepository(null);
    setError(null);
  }, [repositoryId]);

  const reload = useCallback(async () => {
    if (environmentId === null || repositoryId === null) return;
    const { data, error: failure } = await api.GET("/api/v1/environments/{environment_id}/git/repositories/{repository_id}", {
      params: { path: { environment_id: environmentId, repository_id: repositoryId } },
    });
    if (wanted.current !== repositoryId) return;
    if (data) {
      setRepository(data);
      setError(data.error ?? null);
    } else setError(errorMessage(failure, "The project's git state could not be read."));
  }, [environmentId, repositoryId]);

  useEffect(() => {
    if (enabled) void reload();
  }, [reload, refreshKey, enabled]);

  return { repository, error, reload, replace: setRepository };
}
