import { useCallback, useEffect, useRef, useState } from "react";

/** Text for the composer to take over, not sent; `nonce` makes the same text count again. */
export interface DraftRequest {
  text: string;
  nonce: number;
}

/** Puts text into the composer from outside it (a second opinion to build on). */
export function useComposerDraft(conversationId: number | null): {
  request: DraftRequest | null;
  fill: (text: string) => void;
} {
  const [request, setRequest] = useState<DraftRequest | null>(null);
  // Never reset: the composer may stay mounted across conversations and must see every request as new.
  const counter = useRef(0);
  // A request belongs to the conversation it was made in.
  useEffect(() => setRequest(null), [conversationId]);
  const fill = useCallback((text: string) => {
    counter.current += 1;
    setRequest({ text, nonce: counter.current });
  }, []);
  return { request, fill };
}
