import { type RefObject, useEffect, useRef } from "react";

import type { Conversation } from "../../api/client";
import type { ReplyBlock } from "../../components/message/replyBlock";

/**
 * Keep the newest message in view as messages and the streaming reply grow, and scroll to the message a search
 * result asks for. Returns the marker to place after the last message.
 */
export function useMessageScroll(
  conversation: Conversation | null,
  streaming: ReplyBlock[] | null,
  focusMessageId: number | null,
): RefObject<HTMLDivElement | null> {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [conversation?.messages.length, streaming]);

  useEffect(() => {
    if (focusMessageId === null) return;
    document.getElementById(`message-${focusMessageId}`)?.scrollIntoView({ block: "center" });
  }, [conversation?.id, focusMessageId]);

  return endRef;
}
