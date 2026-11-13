import { useEffect, useMemo, useState } from "react";

import { type Conversation, api } from "../../api/client";
import type { components } from "../../api/schema";
import type { CheckpointLinks } from "../../components/message/contexts";
import type { RailSelection } from "./useProjectRail";

type Checkpoint = components["schemas"]["CheckpointOut"];

/** Links from the tool calls of a project conversation to the checkpoints taken before them. */
export function useCheckpointLinks(
  conversation: Conversation | null,
  replyRunning: boolean,
  onOpen: RailSelection["focusCheckpoint"],
): CheckpointLinks | null {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const conversationId = conversation?.project_id ? conversation.id : null;
  const projectId = conversation?.project_id ?? null;
  const messageCount = conversation?.messages.length ?? 0;

  useEffect(() => {
    if (conversationId === null || projectId === null || replyRunning) {
      if (conversationId === null) setCheckpoints([]);
      return;
    }
    let current = true;
    void api
      .GET("/api/v1/projects/{project_id}/checkpoints", {
        params: { path: { project_id: projectId }, query: { conversation_id: conversationId } },
      })
      .then(({ data }) => current && setCheckpoints(data ?? []));
    return () => {
      current = false;
    };
  }, [conversationId, projectId, messageCount, replyRunning]);

  return useMemo(() => {
    if (!conversation || checkpoints.length === 0) return null;
    const byCall = checkpointsByCall(conversation, checkpoints);
    return { forCall: (toolCallId) => byCall.get(toolCallId), open: onOpen };
  }, [conversation, checkpoints, onOpen]);
}

/** Each changing tool call gets the oldest unused checkpoint with the same call key. */
function checkpointsByCall(conversation: Conversation, checkpoints: Checkpoint[]): Map<string, number> {
  const waiting = new Map<string, number[]>();
  for (const checkpoint of [...checkpoints].reverse()) {
    if (checkpoint.kind !== "action" || !checkpoint.call_key) continue;
    waiting.set(checkpoint.call_key, [...(waiting.get(checkpoint.call_key) ?? []), checkpoint.id]);
  }
  const byCall = new Map<string, number>();
  for (const message of conversation.messages) {
    for (const block of message.content) {
      if (block.type !== "tool_call" || !block.checkpointable) continue;
      const ids = waiting.get(block.call_key);
      const id = ids?.shift();
      if (id !== undefined) byCall.set(block.id, id);
    }
  }
  return byCall;
}
