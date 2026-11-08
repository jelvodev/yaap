import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type Replay = components["schemas"]["ReplayOut"];
export type ReplayVariant = components["schemas"]["ReplayIn"];
export type ReplayComparison = components["schemas"]["ReplayComparisonOut"];
export type ReplayCase = components["schemas"]["ReplayCaseOut"];

const POLL_MS = 3000;

export function replayOpen(replay: Replay): boolean {
  return replay.status === "queued" || replay.status === "running";
}

/** The replays of a conversation's runs, and starting one from a model call. */
export function useReplays(conversationId: number) {
  const [replays, setReplays] = useState<Replay[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/conversations/{conversation_id}/replays", {
      params: { path: { conversation_id: conversationId } },
    });
    setReplays(data ?? []);
  }, [conversationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const running = replays.some(replayOpen);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load]);

  const start = useCallback(
    async (stepId: number, variant: ReplayVariant): Promise<Replay | null> => {
      const route = "/api/v1/conversations/{conversation_id}/steps/{step_id}/replays";
      const { data, error: failure } = await api.POST(route, {
        params: { path: { conversation_id: conversationId, step_id: stepId } },
        body: variant,
      });
      if (!data) {
        setError(errorMessage(failure, "The replay could not be started."));
        return null;
      }
      setError(null);
      await load();
      return data;
    },
    [conversationId, load],
  );

  return { replays, error, start, reload: load };
}

/** One replay beside its original, read again while it runs; stopping it and turning it into an eval case. */
export function useReplayComparison(conversationId: number, replayId: number) {
  const [comparison, setComparison] = useState<ReplayComparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<ReplayCase | null>(null);
  const path = { params: { path: { conversation_id: conversationId, replay_id: replayId } } };

  const load = useCallback(async () => {
    const { data, error: failure } = await api.GET("/api/v1/conversations/{conversation_id}/replays/{replay_id}", path);
    if (data) setComparison(data);
    else setError(errorMessage(failure, "The replay could not be loaded."));
  }, [conversationId, replayId]);

  useEffect(() => {
    setComparison(null);
    setSaved(null);
    void load();
  }, [load]);

  const running = comparison !== null && replayOpen(comparison.replay);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, load]);

  const stop = useCallback(async () => {
    await api.POST("/api/v1/conversations/{conversation_id}/replays/{replay_id}/stop", path);
    await load();
  }, [conversationId, replayId, load]);

  const makeCase = useCallback(async () => {
    const { data, error: failure } = await api.POST(
      "/api/v1/conversations/{conversation_id}/replays/{replay_id}/eval-case",
      { ...path, body: {} },
    );
    if (data) {
      setSaved(data);
      setError(null);
      await load();
    } else setError(errorMessage(failure, "The eval case could not be made."));
  }, [conversationId, replayId, load]);

  return { comparison, error, saved, stop, makeCase };
}
