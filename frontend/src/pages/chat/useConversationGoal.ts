import { useCallback, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { components } from "../../api/schema";
import type { Goal } from "../../components/GoalStrip";

export interface ConversationGoal {
  /** The open conversation's goal, unless it was closed from view. */
  shown: Goal | null;
  load: (conversationId: number) => Promise<Goal | null>;
  /** Change the goal; resolves to true when it became active again, so the next turn runs on the server. */
  change: (conversationId: number, body: components["schemas"]["GoalUpdate"]) => Promise<boolean>;
  dismiss: () => void;
}

/** The goal of a conversation; an ended goal can be closed from view. */
export function useConversationGoal(
  conversationId: number | null,
  onError: (message: string) => void,
): ConversationGoal {
  const [goal, setGoal] = useState<Goal | null>(null);
  const [dismissed, setDismissed] = useState<number | null>(null);

  const load = useCallback(async (id: number): Promise<Goal | null> => {
    const { data } = await api.GET("/api/v1/conversations/{conversation_id}/goal", {
      params: { path: { conversation_id: id } },
    });
    const found = data ?? null;
    setGoal(found);
    return found;
  }, []);

  async function change(id: number, body: components["schemas"]["GoalUpdate"]): Promise<boolean> {
    const { data, error: requestError } = await api.PATCH("/api/v1/conversations/{conversation_id}/goal", {
      params: { path: { conversation_id: id } },
      body,
    });
    if (requestError) {
      onError(errorMessage(requestError, "The goal could not be changed."));
      return false;
    }
    if (data) setGoal(data);
    return body.status === "active";
  }

  const shown = goal && goal.conversation_id === conversationId && dismissed !== goal.id ? goal : null;
  return { shown, load, change, dismiss: () => setDismissed(goal?.id ?? null) };
}
