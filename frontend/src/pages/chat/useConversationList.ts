import { useCallback, useEffect, useState } from "react";

import { type ConversationSummary, api } from "../../api/client";
import { notifyIfHidden } from "../../notifications";

export interface ConversationList {
  conversations: ConversationSummary[];
  archived: ConversationSummary[];
  /** Keep a changed summary in the list it now belongs to: archived or active. */
  remember: (summary: ConversationSummary, moveToTop?: boolean) => void;
  forget: (conversationId: number) => void;
}

function upsertSummary(list: ConversationSummary[], summary: ConversationSummary): ConversationSummary[] {
  return [summary, ...list.filter((item) => item.id !== summary.id)];
}

/** The list with the summary replaced where it stands, or added on top when it is new. */
function replaceSummary(list: ConversationSummary[], summary: ConversationSummary): ConversationSummary[] {
  return list.some((item) => item.id === summary.id)
    ? list.map((item) => (item.id === summary.id ? summary : item))
    : [summary, ...list];
}

/** The environment's conversations for the sidebar, active and archived. */
export function useConversationList(environmentId: number): ConversationList {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [archived, setArchived] = useState<ConversationSummary[]>([]);

  const reloadActive = useCallback(async () => {
    const { data } = await api.GET("/api/v1/environments/{environment_id}/conversations", {
      params: { path: { environment_id: environmentId } },
    });
    return data;
  }, [environmentId]);

  useEffect(() => {
    let current = true;
    void reloadActive().then((data) => current && setConversations(data ?? []));
    void api
      .GET("/api/v1/environments/{environment_id}/conversations", {
        params: { path: { environment_id: environmentId }, query: { archived: true } },
      })
      .then(({ data }) => current && setArchived(data ?? []));
    return () => {
      current = false;
    };
  }, [environmentId, reloadActive]);

  // Scheduled task runs: a finished run gives a notification and an unread conversation.
  useEffect(() => {
    let since = new Date().toISOString();
    const timer = window.setInterval(async () => {
      const { data } = await api.GET("/api/v1/environments/{environment_id}/task-runs", {
        params: { path: { environment_id: environmentId }, query: { since } },
      });
      if (!data?.length) return;
      since = data[0].finished_at ?? since;
      for (const run of data) {
        const text = run.summary ?? "The scheduled task finished.";
        notifyIfHidden(`${run.task_name}: ${run.status}`, text, `task-run-${run.id}`);
      }
      const list = await reloadActive();
      if (list) setConversations(list);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [environmentId, reloadActive]);

  const remember = useCallback((summary: ConversationSummary, moveToTop = true) => {
    const place = moveToTop ? upsertSummary : replaceSummary;
    const without = (list: ConversationSummary[]) => list.filter((item) => item.id !== summary.id);
    if (summary.archived_at) {
      setArchived((list) => place(list, summary));
      setConversations(without);
    } else {
      setConversations((list) => place(list, summary));
      setArchived(without);
    }
  }, []);

  const forget = useCallback((conversationId: number) => {
    setConversations((list) => list.filter((item) => item.id !== conversationId));
    setArchived((list) => list.filter((item) => item.id !== conversationId));
  }, []);

  return { conversations, archived, remember, forget };
}
