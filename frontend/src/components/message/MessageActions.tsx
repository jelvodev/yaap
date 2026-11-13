import { type ReactNode, useState } from "react";

import type { Message } from "../../api/client";
import type { SecondOpinions } from "../../pages/chat/useSecondOpinions";
import MakeCardAction from "./MakeCardAction";
import RulePanel, { type RuleTarget } from "../RulePanel";
import type { ToolCallRef } from "../trace/traceOrder";

/** What the user can do with a stored message: edit or retry it, and move between its versions. */
export interface MessageActions {
  busy: boolean;
  onEdit: (message: Message, text: string) => void;
  onRegenerate: (message: Message) => void;
  onShowVersion: (messageId: number) => void;
  // Open the trace of a reply: its steps, sub-runs and what each model call saw; at a tool call's step when given.
  // `tab` opens it on that view instead of the timeline.
  onShowTrace?: (message: Message, toolCall?: ToolCallRef, tab?: "route", stepId?: number) => void;
  // Where Remember as rule can save; absent when the conversation is not loaded.
  ruleTarget?: RuleTarget;
  // The conversation's second opinions and how to ask for one; absent when the conversation is not loaded.
  secondOpinions?: SecondOpinions;
  // Put text into the composer, not sent (a second opinion to build on).
  onUseInConversation?: (text: string) => void;
  // Open a review in the project's Reviews tab.
  onOpenReview?: (reviewId: number) => void;
  // The conversation's last reply and the actions under it that act on the whole conversation (eval case, learning).
  lastReply?: { messageId: number; actions: ReactNode };
}

export function VersionSwitch({ message, actions }: { message: Message; actions: MessageActions }) {
  const versions = message.version_ids ?? [message.id];
  if (versions.length < 2) return null;
  const index = versions.indexOf(message.id);
  return (
    <span className="version-switch" aria-label="Versions of this message">
      <button
        type="button"
        aria-label="Previous version"
        disabled={actions.busy || index <= 0}
        onClick={() => actions.onShowVersion(versions[index - 1])}
      >
        ‹
      </button>
      <span>
        {index + 1}/{versions.length}
      </span>
      <button
        type="button"
        aria-label="Next version"
        disabled={actions.busy || index >= versions.length - 1}
        onClick={() => actions.onShowVersion(versions[index + 1])}
      >
        ›
      </button>
    </span>
  );
}

/**
 * The row under a user message: its versions, Edit, and Remember as rule with the rule panel it opens. While the
 * message is edited the row hides, and an open rule panel stays.
 */
export function UserMessageActions({
  message,
  text,
  actions,
  editing,
  onEdit,
}: {
  message: Message;
  /** The message's text; without text there is nothing to edit or remember. */
  text: string;
  actions: MessageActions;
  editing: boolean;
  onEdit: () => void;
}) {
  const [remembering, setRemembering] = useState(false);
  return (
    <>
      {!editing && (
        <div className="message-actions">
          <VersionSwitch message={message} actions={actions} />
          {text && (
            <button type="button" disabled={actions.busy} onClick={onEdit}>
              Edit
            </button>
          )}
          <MakeCardAction messageId={message.id} target={actions.ruleTarget} />
          {text && actions.ruleTarget && !remembering && (
            <button type="button" onClick={() => setRemembering(true)}>
              Remember as rule
            </button>
          )}
        </div>
      )}
      {remembering && actions.ruleTarget && (
        <RulePanel messageId={message.id} target={actions.ruleTarget} onClose={() => setRemembering(false)} />
      )}
    </>
  );
}
