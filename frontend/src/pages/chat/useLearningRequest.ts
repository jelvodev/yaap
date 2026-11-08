import { useState } from "react";

import { api, errorMessage } from "../../api/client";

export interface LearningRequest {
  busy: boolean;
  /** What the request found, or why it failed; null before it ran. */
  result: string | null;
  request: () => Promise<void>;
}

/** Ask for learning proposals from a conversation now. They wait in the Learning view of Memory; nothing
 * is applied without the user. */
export function useLearningRequest(conversationId: number): LearningRequest {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  async function request() {
    setBusy(true);
    setResult(null);
    const { data, error } = await api.POST("/api/v1/conversations/{conversation_id}/learning-review", {
      params: { path: { conversation_id: conversationId } },
    });
    setBusy(false);
    if (error || !data) setResult(errorMessage(error, "No proposals could be made."));
    else if (data.length === 0) setResult("Nothing new to learn");
    else setResult(`${data.length} proposal${data.length === 1 ? "" : "s"} in Memory · Learning`);
  }

  return { busy, result, request };
}
