import { useEffect, useState } from "react";

import { api, type Behind, errorMessage } from "../../api/client";

// Remounts and re-renders reuse the first answer; a trim or summary noted just after the reply shows after a reload.
const cache = new Map<number, Behind>();

/** The labels and call reasons under a stored reply, fetched once per message. */
export function useBehind(conversationId: number, messageId: number): { behind: Behind | null; error: string | null } {
  const [behind, setBehind] = useState<Behind | null>(cache.get(messageId) ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    const cached = cache.get(messageId);
    if (cached) {
      setBehind(cached);
      return;
    }
    setBehind(null);
    let current = true;
    void api
      .GET("/api/v1/conversations/{conversation_id}/messages/{message_id}/behind", {
        params: { path: { conversation_id: conversationId, message_id: messageId } },
      })
      .then(({ data, error: failure }) => {
        if (!current) return;
        if (data) {
          cache.set(messageId, data);
          setBehind(data);
        } else setError(errorMessage(failure, "What happened behind this reply could not be loaded."));
      });
    return () => {
      current = false;
    };
  }, [conversationId, messageId]);

  return { behind, error };
}
