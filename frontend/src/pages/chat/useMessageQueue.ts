import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { QueuedMessage } from "../../components/QueueStrip";

export interface QueueSelection {
  item_id?: number;
  all?: boolean;
}

export interface MessageQueue {
  items: QueuedMessage[];
  /** Queue a message; resolves to false when the server refused it. */
  add: (conversationId: number, text: string, attachmentIds: number[]) => Promise<boolean>;
  /** Refresh and return the server's current queue. */
  refresh: (conversationId: number) => Promise<QueuedMessage[]>;
  /** Persist a send-now selection and let the server resume it after stopping the current reply. */
  sendNow: (conversationId: number, selection: QueueSelection) => Promise<boolean>;
  /** Hide the selected messages at once; the returned function puts them back if sending fails. */
  withdraw: (selection: QueueSelection) => () => void;
  remove: (item: QueuedMessage) => Promise<void>;
  /** Give a queued message to the running agent. */
  steer: (item: QueuedMessage) => Promise<void>;
}

/** Messages queued behind the running reply of the open conversation. */
export function useMessageQueue(conversationId: number | null, onError: (message: string) => void): MessageQueue {
  const [items, setItems] = useState<QueuedMessage[]>([]);

  const load = useCallback(async (id: number): Promise<QueuedMessage[]> => {
    const { data } = await api.GET("/api/v1/conversations/{conversation_id}/queue", {
      params: { path: { conversation_id: id } },
    });
    const current = data ?? [];
    setItems(current);
    return current;
  }, []);

  useEffect(() => {
    if (conversationId) void load(conversationId);
    else setItems([]);
  }, [conversationId, load]);

  async function add(id: number, text: string, attachmentIds: number[]): Promise<boolean> {
    const { error: queueError } = await api.POST("/api/v1/conversations/{conversation_id}/queue", {
      params: { path: { conversation_id: id } },
      body: { text, attachment_ids: attachmentIds },
    });
    if (queueError) {
      onError(errorMessage(queueError, "The message could not be queued."));
      return false;
    }
    await load(id);
    return true;
  }

  function withdraw(selection: QueueSelection): () => void {
    const before = items;
    setItems((current) => (selection.all ? [] : current.filter((entry) => entry.id !== selection.item_id)));
    return () => setItems(before);
  }

  async function remove(item: QueuedMessage) {
    if (!conversationId) return;
    await api.DELETE("/api/v1/conversations/{conversation_id}/queue/{item_id}", {
      params: { path: { conversation_id: conversationId, item_id: item.id } },
    });
    await load(conversationId);
  }

  async function sendNow(id: number, selection: QueueSelection): Promise<boolean> {
    const { error: sendError } = await api.POST("/api/v1/conversations/{conversation_id}/queue/send-now", {
      params: { path: { conversation_id: id } },
      body: { item_id: selection.item_id ?? null, all: selection.all ?? false },
    });
    if (sendError) {
      onError(errorMessage(sendError, "The queued message could not be sent now."));
      return false;
    }
    return true;
  }

  async function steer(item: QueuedMessage) {
    if (!conversationId) return;
    const { error: steerError } = await api.POST("/api/v1/conversations/{conversation_id}/steer", {
      params: { path: { conversation_id: conversationId } },
      body: { text: "", item_id: item.id },
    });
    if (steerError) onError(errorMessage(steerError, "The message could not be sent to YAAP."));
    await load(conversationId);
  }

  return { items, add, refresh: load, withdraw, remove, sendNow, steer };
}
