import { useEffect, useState } from "react";

import { type RouteOut, api, errorMessage } from "../../api/client";

// Chips remount with their message; they reuse the route fetched first. The trace tab always asks again.
const cache = new Map<number, RouteOut>();
const CACHE_LIMIT = 100;

function remember(messageId: number, route: RouteOut) {
  cache.delete(messageId);
  cache.set(messageId, route);
  if (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value ?? messageId);
}

/** Drop the route kept for a reply, so the next chip asks again (stops can still be added after the reply). */
export function forgetRoute(messageId: number): void {
  cache.delete(messageId);
}

/** The route of a stored reply: its stops, time and cost. */
export function useRoute(
  conversationId: number,
  messageId: number,
  reuse = false,
): { route: RouteOut | null; error: string | null } {
  const [route, setRoute] = useState<RouteOut | null>((reuse && cache.get(messageId)) || null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    const cached = reuse ? cache.get(messageId) : undefined;
    if (cached) {
      setRoute(cached);
      return;
    }
    setRoute(null);
    let current = true;
    void api
      .GET("/api/v1/conversations/{conversation_id}/messages/{message_id}/route", {
        params: { path: { conversation_id: conversationId, message_id: messageId } },
      })
      .then(({ data, error: failure }) => {
        if (!current) return;
        if (data) {
          remember(messageId, data);
          setRoute(data);
        } else setError(errorMessage(failure, "The route of this reply could not be loaded."));
      });
    return () => {
      current = false;
    };
  }, [conversationId, messageId, reuse]);

  return { route, error };
}
