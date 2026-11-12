import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";

export type Breakdown = components["schemas"]["ContextBreakdownOut"];

/** What the conversation's context holds now, read again when version changes or reload is called. */
export function useContextBreakdown(
  conversationId: number,
  version: unknown,
): { breakdown: Breakdown | null; error: string | null; reload: () => void } {
  const [breakdown, setBreakdown] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);

  // Another conversation's answer is never shown; a new reply or reload keeps the old one until the new arrives.
  useEffect(() => {
    setBreakdown(null);
    setError(null);
  }, [conversationId]);

  useEffect(() => {
    // A newer request supersedes this one; its answer is dropped.
    let current = true;
    void api
      .GET("/api/v1/conversations/{conversation_id}/context", { params: { path: { conversation_id: conversationId } } })
      .then(({ data, error: failure }) => {
        if (!current) return;
        setBreakdown(data ?? null);
        setError(data ? null : errorMessage(failure));
      });
    return () => {
      current = false;
    };
  }, [conversationId, version, reloads]);

  const reload = useCallback(() => setReloads((value) => value + 1), []);
  return { breakdown, error, reload };
}
