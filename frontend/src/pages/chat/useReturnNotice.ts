import { useCallback, useEffect, useState } from "react";

import { type ConversationSummary, api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type ReturnNoticeBody = components["schemas"]["ReturnNoticeBodyOut"];
type LinkedConversation = components["schemas"]["LinkedConversationOut"];

export interface ReturnNoticeState {
  /** The notice to show before the next message; null while a reply runs, once dismissed, or when none is due. */
  notice: ReturnNoticeBody | null;
  pending: "summarise" | "continue" | null;
  error: string | null;
  dismiss: () => void;
  summarise: () => Promise<void>;
  continueFromSummary: () => Promise<void>;
}

interface ReturnNoticeCallbacks {
  /** The conversation changed on the server (a summary was added): read it again. */
  onChanged: () => void;
  /** A new conversation was made from a summary. */
  onContinued: (conversation: ConversationSummary) => void;
}

/**
 * The notice after a pause long enough for the prompt cache to expire. It is read again whenever `refreshKey`
 * changes (after each reply) and hidden while a reply runs; nothing happens unless the person chooses.
 */
export function useReturnNotice(
  conversationId: number | null,
  refreshKey: string | number,
  busy: boolean,
  { onChanged, onContinued }: ReturnNoticeCallbacks,
): ReturnNoticeState {
  const [notice, setNotice] = useState<ReturnNoticeBody | null>(null);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [pending, setPending] = useState<ReturnNoticeState["pending"]>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setNotice(null);
    setError(null);
    if (conversationId === null || busy) return;
    let current = true;
    void api
      .GET("/api/v1/conversations/{conversation_id}/return-notice", {
        params: { path: { conversation_id: conversationId } },
      })
      .then(({ data }) => {
        if (current) setNotice(data?.notice ?? null);
      });
    return () => {
      current = false;
    };
  }, [conversationId, refreshKey, busy]);

  const key = conversationId !== null && notice ? `${conversationId}:${notice.idle_since}` : null;
  const dismiss = useCallback(() => {
    if (key) setDismissed((keys) => [...keys, key]);
  }, [key]);

  async function summarise() {
    if (conversationId === null) return;
    setPending("summarise");
    setError(null);
    const { error: failure } = await api.POST("/api/v1/conversations/{conversation_id}/summary", {
      params: { path: { conversation_id: conversationId } },
    });
    setPending(null);
    if (failure) return setError(errorMessage(failure, "The summary could not be written."));
    dismiss();
    onChanged();
  }

  async function continueFromSummary() {
    if (conversationId === null) return;
    setPending("continue");
    setError(null);
    const { data, error: failure } = await api.POST("/api/v1/conversations/{conversation_id}/continue-from-summary", {
      params: { path: { conversation_id: conversationId } },
    });
    setPending(null);
    if (failure || !data) return setError(errorMessage(failure, "The new conversation could not be made."));
    onContinued(data);
  }

  const shown = notice && key && !dismissed.includes(key) && !busy ? notice : null;
  return { notice: shown, pending, error, dismiss, summarise, continueFromSummary };
}

/** The conversation this one continues, and the ones that continue it (the newest last). */
export function useContinuationLinks(conversationId: number | null): {
  continuedFrom: LinkedConversation | null;
  continuedIn: LinkedConversation[];
} {
  const [continuedFrom, setContinuedFrom] = useState<LinkedConversation | null>(null);
  const [continuedIn, setContinuedIn] = useState<LinkedConversation[]>([]);

  useEffect(() => {
    setContinuedFrom(null);
    setContinuedIn([]);
    if (conversationId === null) return;
    let current = true;
    void api
      .GET("/api/v1/conversations/{conversation_id}/continuation", {
        params: { path: { conversation_id: conversationId } },
      })
      .then(({ data }) => {
        if (!current || !data) return;
        setContinuedFrom(data.continued_from ?? null);
        setContinuedIn(data.continued_in);
      });
    return () => {
      current = false;
    };
  }, [conversationId]);

  return { continuedFrom, continuedIn };
}
