import { useState } from "react";

import { api, errorMessage } from "../../api/client";

export interface MakeCardState {
  /** The card made from the message, once it exists. */
  made: number | null;
  error: string | null;
  busy: boolean;
  make: () => Promise<void>;
}

/** Make a card on the project board from one message of a conversation. */
export function useMakeCard(conversationId: number, messageId: number): MakeCardState {
  const [made, setMade] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function make(): Promise<void> {
    setBusy(true);
    setError(null);
    const { data, error: failed } = await api.POST(
      "/api/v1/conversations/{conversation_id}/messages/{message_id}/card",
      { params: { path: { conversation_id: conversationId, message_id: messageId } } },
    );
    setBusy(false);
    if (data) setMade(data.id);
    else setError(errorMessage(failed, "The card could not be made."));
  }

  return { made, error, busy, make };
}
