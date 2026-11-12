import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { SecondOpinion, SecondOpinionPreview } from "../../components/message/secondOpinionText";

const POLL_MS = 3000;

/** The open conversation's second opinions, and what asking for one needs. */
export interface SecondOpinions {
  conversationId: number;
  opinions: SecondOpinion[];
  preview: (messageId: number) => Promise<{ preview?: SecondOpinionPreview; error?: string }>;
  start: (messageId: number, allowSameVendor: boolean) => Promise<{ error?: string }>;
}

/** A conversation's second opinions, read again every few seconds while one runs. Null without a conversation. */
export function useSecondOpinions(conversationId: number | null): SecondOpinions | null {
  const [loaded, setLoaded] = useState<{ conversationId: number; opinions: SecondOpinion[] } | null>(null);

  const reload = useCallback(async (): Promise<boolean> => {
    if (conversationId === null) return false;
    const { data } = await api.GET("/api/v1/conversations/{conversation_id}/second-opinions", {
      params: { path: { conversation_id: conversationId } },
    });
    if (data) setLoaded({ conversationId, opinions: data });
    return Boolean(data);
  }, [conversationId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const opinions = loaded && loaded.conversationId === conversationId ? loaded.opinions : [];
  const anyRunning = opinions.some((opinion) => opinion.status === "running");
  useEffect(() => {
    if (!anyRunning) return;
    const timer = window.setInterval(() => void reload(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [anyRunning, reload]);

  const preview = useCallback(
    async (messageId: number) => {
      if (conversationId === null) return { error: "No conversation is open." };
      const { data, error } = await api.GET(
        "/api/v1/conversations/{conversation_id}/messages/{message_id}/second-opinion",
        { params: { path: { conversation_id: conversationId, message_id: messageId } } },
      );
      return data ? { preview: data } : { error: errorMessage(error, "The second opinion could not be prepared.") };
    },
    [conversationId],
  );

  const start = useCallback(
    async (messageId: number, allowSameVendor: boolean) => {
      if (conversationId === null) return { error: "No conversation is open." };
      const { data, error } = await api.POST(
        "/api/v1/conversations/{conversation_id}/messages/{message_id}/second-opinion",
        {
          params: { path: { conversation_id: conversationId, message_id: messageId } },
          body: { allow_same_vendor: allowSameVendor },
        },
      );
      if (!data) return { error: errorMessage(error, "The second opinion could not start.") };
      // The new opinion shows once the list loads: try again once, so polling starts while it runs.
      if (!(await reload()) && !(await reload())) {
        return { error: "The second opinion started, but the list could not be loaded. Reload the page to see it." };
      }
      return {};
    },
    [conversationId, reload],
  );

  return conversationId === null ? null : { conversationId, opinions, preview, start };
}
