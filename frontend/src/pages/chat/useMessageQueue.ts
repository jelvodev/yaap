import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../../api/client";
import type { QueuedMessage } from "../../components/QueueStrip";

/** A message taken from the queue to be sent now. */
export interface TakenMessage {
  text: string;
  attachment_ids: number[];
}

export interface MessageQueue {
  items: QueuedMessage[];
  /** Queue a message; resolves to false when the server refused it. */
  add: (conversationId: number, text: string, attachmentIds: number[]) => Promise<boolean>;
  /** Take one queued message, or all of them joined, off the queue. */
  take: (conversationId: number, body: { item_id?: number; all?: boolean }) => Promise<TakenMessage | null>;
  remove: (item: QueuedMessage) => Promise<void>;
  /** Give a queued message to the running agent. */
  steer: (item: QueuedMessage) => Promise<void>;
}

/** Messages queued behind the running reply of the open conversation. */
export function useMessageQueue(conversationId: number | null, onError: (message: string) => void): MessageQueue {
  const [items, setItems] = useState<QueuedMessage[]>([]);

  const load = useCallback(async (id: number) => {
    const { data } = await api.GET("/api/v1/conversations/{conversation_id}/queue", {
      params: { path: { conversation_id: id } },
    });
    setItems(data ?? []);
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

  async function take(id: number, body: { item_id?: number; all?: boolean }): Promise<TakenMessage | null> {
    const { data } = await api.POST("/api/v1/conversations/{conversation_id}/queue/take", {
      params: { path: { conversation_id: id } },
      body: { all: body.all ?? false, item_id: body.item_id ?? null },
    });
    await load(id);
    return data ?? null;
  }

  async function remove(item: QueuedMessage) {
    if (!conversationId) return;
    await api.DELETE("/api/v1/conversations/{conversation_id}/queue/{item_id}", {
      params: { path: { conversation_id: conversationId, item_id: item.id } },
    });
    await load(conversationId);
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

  return { items, add, take, remove, steer };
}
