import { useMemo, useState } from "react";

import type { MessageStreamHandlers } from "../../api/message-stream";
import { type ReplyBlock, appendText } from "../../components/message/replyBlock";
import { notifyIfHidden } from "../../notifications";
import {
  addApproval,
  addSteering,
  addToolCall,
  appendToolOutput,
  dropStreamedText,
  resolveApproval,
  setToolResult,
  showDelegateProgress,
} from "./replyBlocks";

/** The stream events that only change the blocks of the reply as it comes in. */
export type BlockHandlers = Pick<
  MessageStreamHandlers,
  | "onReasoningDelta"
  | "onTextDelta"
  | "onCallRestarted"
  | "onToolCall"
  | "onDelegateProgress"
  | "onSteering"
  | "onToolOutput"
  | "onToolResult"
  | "onApprovalRequest"
  | "onApprovalResolved"
>;

export interface ReplyBlocks {
  /** The reply streaming in, or null while none is followed. */
  blocks: ReplyBlock[] | null;
  /** Start showing a new reply. */
  start: () => void;
  /** Show a reply, keeping what already arrived of it. */
  keep: () => void;
  clear: () => void;
  handlers: BlockHandlers;
}

/** The blocks of the reply this page follows, as its events arrive. */
export function useReplyBlocks(): ReplyBlocks {
  const [blocks, setBlocks] = useState<ReplyBlock[] | null>(null);

  const actions = useMemo(() => {
    // Events that arrive while no reply is shown change nothing.
    const update = (change: (current: ReplyBlock[]) => ReplyBlock[]) =>
      setBlocks((current) => current && change(current));
    const handlers: BlockHandlers = {
      onReasoningDelta: (delta) => update((current) => appendText(current, "reasoning", delta)),
      onTextDelta: (delta) => update((current) => appendText(current, "text", delta)),
      onCallRestarted: () => update(dropStreamedText),
      onToolCall: (call) => update((current) => addToolCall(current, call)),
      onDelegateProgress: (label, step) => update((current) => showDelegateProgress(current, label, step)),
      onSteering: (text) => update((current) => addSteering(current, text)),
      onToolOutput: (key, text) => update((current) => appendToolOutput(current, key, text)),
      onToolResult: (result) => update((current) => setToolResult(current, result)),
      onApprovalRequest: (request) => {
        notifyIfHidden("YAAP waits for your approval", request.summary, request.approval_id);
        update((current) => addApproval(current, request));
      },
      onApprovalResolved: (approvalId, decision) => update((current) => resolveApproval(current, approvalId, decision)),
    };
    return {
      start: () => setBlocks([]),
      keep: () => setBlocks((current) => current ?? []),
      clear: () => setBlocks(null),
      handlers,
    };
  }, []);

  return { blocks, ...actions };
}
