import { useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type TokenUsage = components["schemas"]["TokenUsageOut"];

/** Where the tokens of a conversation went, fetched once per conversation. */
export function useTokenUsage(conversationId: number): { usage: TokenUsage | null; error: string | null } {
  const [usage, setUsage] = useState<TokenUsage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setUsage(null);
    setError(null);
    void api
      .GET("/api/v1/conversations/{conversation_id}/token-usage", {
        params: { path: { conversation_id: conversationId } },
      })
      .then(({ data, error: failure }) => {
        if (!current) return;
        if (data) setUsage(data);
        else setError(errorMessage(failure, "The token usage could not be loaded."));
      });
    return () => {
      current = false;
    };
  }, [conversationId]);

  return { usage, error };
}
