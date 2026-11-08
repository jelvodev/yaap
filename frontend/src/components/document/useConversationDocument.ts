import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { DocumentEvent } from "../../api/message-stream";
import type { components } from "../../api/schema";

export type SharedDocument = components["schemas"]["DocumentOut"];
export type Decision = "accept" | "rollback";

/**
 * The document open beside a conversation: which one it is, opening and closing it, the reply's
 * document events, and the user's decision on the agent's change. `version` counts changes made elsewhere (by the
 * agent, or a roll back), so the panel reads the text again; `writing` is true while the agent writes it; `delivered`
 * counts the times the agent got the user's saved changes.
 */
export function useConversationDocument(conversationId: number | null, inProject: boolean, replyRunning: boolean) {
  const [document, setDocument] = useState<SharedDocument | null>(null);
  const [version, setVersion] = useState(0);
  const [writing, setWriting] = useState(false);
  const [delivered, setDelivered] = useState(0);
  const current = useRef(conversationId);
  current.current = conversationId;

  const load = useCallback(async () => {
    if (conversationId === null || !inProject) {
      setDocument(null);
      return;
    }
    const { data } = await api.GET("/api/v1/conversations/{conversation_id}/document", {
      params: { path: { conversation_id: conversationId } },
    });
    if (current.current === conversationId) setDocument(data ?? null);
  }, [conversationId, inProject]);

  useEffect(() => {
    setDocument(null);
    setWriting(false);
    void load();
  }, [load]);

  // When a reply ends, its last change is in: the review shows, and nothing is being written any more.
  useEffect(() => {
    if (replyRunning) return;
    setWriting(false);
    void load();
  }, [replyRunning, load]);

  const open = useCallback(
    async (path: string): Promise<string | null> => {
      if (conversationId === null) return "Open a conversation first.";
      const { data, error: failure } = await api.PUT("/api/v1/conversations/{conversation_id}/document", {
        params: { path: { conversation_id: conversationId } },
        body: { path },
      });
      if (!data) return errorMessage(failure, "The document could not be opened.");
      setDocument(data);
      setVersion((count) => count + 1);
      return null;
    },
    [conversationId],
  );

  const close = useCallback(async () => {
    if (conversationId === null) return;
    await api.DELETE("/api/v1/conversations/{conversation_id}/document", {
      params: { path: { conversation_id: conversationId } },
    });
    setDocument(null);
  }, [conversationId]);

  /** Accept the agent's change or roll it back; the error to show, or null. */
  const decide = useCallback(
    async (decision: Decision): Promise<string | null> => {
      if (conversationId === null || !document) return null;
      const options = { params: { path: { conversation_id: conversationId } }, body: { hash: document.hash } };
      const { data, error: failure } =
        decision === "accept"
          ? await api.POST("/api/v1/conversations/{conversation_id}/document/accept", options)
          : await api.POST("/api/v1/conversations/{conversation_id}/document/rollback", options);
      if (!data) {
        void load();
        setVersion((count) => count + 1);
        return errorMessage(failure, "That did not work.");
      }
      setDocument(data);
      if (decision === "rollback") setVersion((count) => count + 1);
      return null;
    },
    [conversationId, document, load],
  );

  const onEvent = useCallback(
    (event: DocumentEvent) => {
      if (event.kind === "writing") setWriting(true);
      if (event.kind === "seen") setDelivered((count) => count + 1);
      if (event.kind === "open" || event.kind === "change") {
        if (event.kind === "change") {
          setWriting(false);
          setVersion((count) => count + 1);
        }
        void load();
      }
    },
    [load],
  );

  return { document, version, writing, delivered, open, close, decide, reload: load, onEvent };
}
