import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";

export interface RunningReplies {
  /** Conversations of this environment with a reply in progress. */
  running: number[];
  reload: () => Promise<void>;
  markRunning: (conversationId: number) => void;
}

/** The conversations of an environment whose reply runs on the server now. */
export async function listRunningReplies(environmentId: number): Promise<number[]> {
  const { data } = await api.GET("/api/v1/environments/{environment_id}/replies", {
    params: { path: { environment_id: environmentId } },
  });
  return data ?? [];
}

/** Which conversations have a reply in progress; checked now and then while any do. */
export function useRunningReplies(environmentId: number): RunningReplies {
  const [running, setRunning] = useState<number[]>([]);

  const reload = useCallback(async () => {
    setRunning(await listRunningReplies(environmentId));
  }, [environmentId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (running.length === 0) return;
    const timer = window.setInterval(() => void reload(), 3000);
    return () => window.clearInterval(timer);
  }, [running.length, reload]);

  const markRunning = useCallback((conversationId: number) => {
    setRunning((current) => (current.includes(conversationId) ? current : [...current, conversationId]));
  }, []);

  return { running, reload, markRunning };
}
