import { useCallback, useEffect, useState } from "react";

import { type GitRepository, api, errorMessage } from "../../api/client";

export type SyncAction = "fetch" | "pull" | "push";
type Busy = "suggest" | "commit" | "discard" | SyncAction;

const SYNC_DONE: Record<SyncAction, string> = { fetch: "Fetched.", pull: "Pulled.", push: "Pushed." };

export interface Notice {
  error: boolean;
  text: string;
}

export interface GitChanges {
  /** The whole uncommitted diff, or null while it is read. */
  diff: string | null;
  /** The action that runs, or null. */
  busy: Busy | null;
  notice: Notice | null;
  /** The utility model's commit message for the changes, or null when it proposed none (the notice says why). */
  suggestMessage: () => Promise<string | null>;
  /** Commit every change; true when it worked. */
  commit: (message: string) => Promise<boolean>;
  sync: (action: SyncAction) => Promise<boolean>;
  /** Discard the uncommitted changes of one file, or of all (null), after a checkpoint; true when it worked. */
  discard: (path: string | null) => Promise<boolean>;
  setNotice: (notice: Notice | null) => void;
}

/**
 * The uncommitted diff of a repository and the git actions on it: suggest a message, commit, discard, fetch, pull,
 * push.
 */
export function useGitChanges(
  environmentId: number,
  repository: GitRepository,
  onChanged: (updated: GitRepository) => void,
): GitChanges {
  const [diff, setDiff] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const path = { params: { path: { environment_id: environmentId, repository_id: repository.id } } };
  const changeKey = repository.changes.map((change) => `${change.state}:${change.path}`).join("|");

  const loadDiff = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/diff", {
      params: { path: { environment_id: environmentId, repository_id: repository.id } },
    });
    setDiff(data?.diff ?? "");
  }, [environmentId, repository.id]);

  useEffect(() => {
    void loadDiff();
  }, [loadDiff, changeKey]);

  async function act(name: Busy, request: () => Promise<{ data?: GitRepository; error?: unknown }>, done: string) {
    setBusy(name);
    setNotice(null);
    const { data, error } = await request();
    setBusy(null);
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error) });
      return false;
    }
    setNotice({ error: false, text: done });
    onChanged(data);
    return true;
  }

  async function suggestMessage() {
    setBusy("suggest");
    setNotice(null);
    const { data, error } = await api.POST(
      "/api/v1/environments/{environment_id}/git/repositories/{repository_id}/commit-message",
      path,
    );
    setBusy(null);
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error, "No message could be proposed.") });
      return null;
    }
    return data.message;
  }

  const commit = (message: string) =>
    act(
      "commit",
      () =>
        api.POST("/api/v1/environments/{environment_id}/git/repositories/{repository_id}/commit", {
          ...path,
          body: { message },
        }),
      "Committed.",
    );

  const sync = (action: SyncAction) =>
    act(
      action,
      () => api.POST(`/api/v1/environments/{environment_id}/git/repositories/{repository_id}/${action}`, path),
      SYNC_DONE[action],
    );

  async function discard(file: string | null) {
    setBusy("discard");
    setNotice(null);
    const { data, error } = await api.POST(
      "/api/v1/environments/{environment_id}/git/repositories/{repository_id}/discard",
      { ...path, body: { path: file } },
    );
    setBusy(null);
    if (error || !data) {
      setNotice({ error: true, text: errorMessage(error, "Nothing was discarded.") });
      return false;
    }
    const kept = data.checkpoint_id === null ? "" : " The checkpoint before it is in the Checkpoints tab.";
    setNotice({ error: false, text: `Discarded.${kept}` });
    onChanged(data.repository);
    return true;
  }

  return { diff, busy, notice, suggestMessage, commit, sync, discard, setNotice };
}
