import { useCallback, useRef, useState } from "react";

import type { Message } from "../../api/client";
import ReadAloudButton from "../../speech/ReadAloudButton";
import { BranchIcon, CopyIcon, MoreIcon } from "../icons";
import { useDismiss } from "../useDismiss";
import MakeCardAction from "./MakeCardAction";
import { SecondOpinionAction } from "./SecondOpinionAction";
import { type MessageActions, VersionSwitch } from "./MessageActions";
import { blocksFromMessage } from "./replyBlock";
import "./messageActions.css";

/** The reply's text as the user reads it, without its reasoning and tool calls. */
function replyText(message: Message): string {
  return blocksFromMessage(message)
    .flatMap((block) => (block.type === "text" ? [block.text.trim()] : []))
    .filter(Boolean)
    .join("\n\n");
}

function CopyButton({ message }: { message: Message }) {
  const [copied, setCopied] = useState(false);
  const text = replyText(message);
  if (!text) return null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // The browser refused the clipboard (no permission, or not a secure page): nothing was copied.
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      className={copied ? "message-action-labelled" : "message-action-icon"}
      aria-label="Copy the reply"
      title="Copy the reply's text"
      onClick={() => void copy()}
    >
      <CopyIcon size={13} />
      {copied && "Copied"}
    </button>
  );
}

/** The reply's ⋯: its trace and Read aloud, used less often than the actions in the row. */
function ReplyMoreMenu({ message, actions }: { message: Message; actions: MessageActions }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement | null>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  return (
    <span className="reply-more" ref={ref}>
      <button
        type="button"
        className="message-action-icon"
        aria-label="More actions for this reply"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <MoreIcon size={13} />
      </button>
      {open && (
        <div className="conversation-menu reply-more-menu" role="menu">
          {actions.onShowTrace && (
            <button
              type="button"
              role="menuitem"
              title="The steps of this reply, with duration, cost and what each model call saw"
              onClick={() => {
                setOpen(false);
                actions.onShowTrace?.(message);
              }}
            >
              Show trace
            </button>
          )}
          <ReadAloudButton messageId={message.id} />
        </div>
      )}
    </span>
  );
}

/**
 * The row under a reply: its versions, Copy, the conversation's own actions under its last reply (Save as eval case,
 * Learn from this), Branch (Try again: a new version beside this one) and a ⋯ with Show trace and Read aloud. The
 * last reply's row always shows; the others show on hover.
 */
export function ReplyActions({ message, actions }: { message: Message; actions: MessageActions }) {
  const last = actions.lastReply?.messageId === message.id ? actions.lastReply : undefined;
  return (
    <div className={`message-actions reply-actions${last ? " reply-actions-last" : ""}`}>
      <VersionSwitch message={message} actions={actions} />
      <CopyButton message={message} />
      {last?.actions}
      <MakeCardAction messageId={message.id} target={actions.ruleTarget} />
      {message.status === "complete" && message.id > 0 && <SecondOpinionAction message={message} actions={actions} />}
      <button
        type="button"
        className="message-action-icon"
        aria-label="Branch: try again"
        title="Try again: a new version of this reply beside this one; switch between them with ‹ ›"
        disabled={actions.busy}
        onClick={() => actions.onRegenerate(message)}
      >
        <BranchIcon size={13} />
      </button>
      <ReplyMoreMenu message={message} actions={actions} />
    </div>
  );
}
