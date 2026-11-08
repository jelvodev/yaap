import { useCallback, useRef, useState } from "react";

import type { Conversation } from "../../api/client";
import ConversationDeleteConfirm from "../../components/ConversationDeleteConfirm";
import { MoreIcon } from "../../components/icons";
import { reviewOutcome, useReviews } from "../../components/project/useReviews";
import { useDismiss } from "../../components/useDismiss";
import EvalCaseDialog from "./EvalCaseDialog";
import { useLearningRequest } from "./useLearningRequest";

export interface ConversationMenuProps {
  conversation: Conversation;
  /** A reply streams in: the actions that read the whole conversation wait. */
  busy: boolean;
  /** The conversation's last stored reply, for Show trace; null before the first one. */
  lastReplyId: number | null;
  onShowTrace: (messageId: number) => void;
  /** Rename in place, in the heading. */
  onRename: () => void;
  /** Archive or restore; resolves to the error to show, or null. */
  onArchive: (archived: boolean) => Promise<string | null>;
  /** Delete for good, after the confirmation; resolves to the error to show, or null. */
  onDelete: () => Promise<string | null>;
  /** Show a review of this conversation's changes in Evals · Reviews. */
  onOpenReview: (reviewId: number) => void;
}

function exportUrl(conversationId: number, format: "markdown" | "json"): string {
  return `/api/v1/conversations/${conversationId}/export?format=${format}`;
}

/**
 * The heading's ⋯: export, the actions that act on the whole conversation (eval case, learning, the last reply's
 * trace, the latest review), and rename, archive and delete. Learning's outcome shows beside the button.
 */
export default function ConversationMenu(props: ConversationMenuProps) {
  const { conversation, busy } = props;
  const [mode, setMode] = useState<"closed" | "menu" | "delete">("closed");
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [evalOpen, setEvalOpen] = useState(false);
  const learning = useLearningRequest(conversation.id);
  const ref = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => {
    setMode("closed");
    setMessage(null);
  }, []);
  useDismiss(ref, mode !== "closed", close);
  const projectId = conversation.project_id ?? null;
  const hasMessages = conversation.messages.length > 0;
  const archived = Boolean(conversation.archived_at);

  async function run(action: () => Promise<string | null>) {
    setWorking(true);
    const problem = await action();
    setWorking(false);
    setMessage(problem);
    if (!problem) setMode("closed");
  }

  /** A menu item that closes the menu before it acts. */
  const choose = (action: () => void) => () => {
    close();
    action();
  };

  return (
    <div className="chat-heading-menu" ref={ref}>
      {learning.result && (
        <span className="chip" role="status">
          {learning.result}
        </span>
      )}
      <button
        type="button"
        className="icon-button chat-heading-more"
        aria-label="Conversation actions"
        aria-haspopup="menu"
        aria-expanded={mode !== "closed"}
        onClick={() => (mode === "closed" ? setMode("menu") : close())}
      >
        <MoreIcon size={16} />
      </button>
      {mode === "menu" && (
        <div className="conversation-menu chat-heading-popover" role="menu">
          <a role="menuitem" href={exportUrl(conversation.id, "markdown")} download onClick={close}>
            Export as Markdown
          </a>
          <a role="menuitem" href={exportUrl(conversation.id, "json")} download onClick={close}>
            Export as JSON
          </a>
          {projectId !== null && hasMessages && (
            <>
              <hr />
              <button
                type="button"
                role="menuitem"
                disabled={busy}
                title="Make an eval case of this conversation: its first message, the start and checks to edit"
                onClick={choose(() => setEvalOpen(true))}
              >
                Save as eval case
              </button>
              <button
                type="button"
                role="menuitem"
                disabled={busy || learning.busy}
                title="Propose rules, memory lines or skill steps from what went wrong in this conversation"
                onClick={choose(() => void learning.request())}
              >
                {learning.busy ? "Learning…" : "Learn from this"}
              </button>
            </>
          )}
          {props.lastReplyId !== null && (
            <button
              type="button"
              role="menuitem"
              title="The steps of the last reply, with duration, cost and what each model call saw"
              onClick={choose(() => props.lastReplyId !== null && props.onShowTrace(props.lastReplyId))}
            >
              Show trace
            </button>
          )}
          {projectId !== null && hasMessages && (
            <LatestReviewItem
              projectId={projectId}
              conversationId={conversation.id}
              onOpen={(reviewId) => {
                close();
                props.onOpenReview(reviewId);
              }}
            />
          )}
          <hr />
          <button type="button" role="menuitem" onClick={choose(props.onRename)}>
            Rename
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={working}
            onClick={() => void run(() => props.onArchive(!archived))}
          >
            {archived ? "Restore from archive" : "Archive"}
          </button>
          <button type="button" role="menuitem" className="danger" onClick={() => setMode("delete")}>
            Delete
          </button>
          {message && <p className="form-error">{message}</p>}
        </div>
      )}
      {mode === "delete" && (
        <ConversationDeleteConfirm
          title={conversation.title}
          busy={working}
          message={message}
          onCancel={() => setMode("menu")}
          onDelete={() => void run(props.onDelete)}
        />
      )}
      {evalOpen && projectId !== null && (
        <EvalCaseDialog conversationId={conversation.id} projectId={projectId} onClose={() => setEvalOpen(false)} />
      )}
    </div>
  );
}

/** The latest review of this conversation's changes, also one a goal or task started by itself. */
function LatestReviewItem({
  projectId,
  conversationId,
  onOpen,
}: {
  projectId: number;
  conversationId: number;
  onOpen: (reviewId: number) => void;
}) {
  const { reviews } = useReviews(projectId, conversationId);
  const latest = reviews?.[0] ?? null;
  if (!latest) return null;
  return (
    <button
      type="button"
      role="menuitem"
      className={latest.high_count > 0 ? "danger" : undefined}
      title={latest.reason ?? "Open the latest review beside its diff"}
      onClick={() => onOpen(latest.id)}
    >
      Latest review · {reviewOutcome(latest)}
    </button>
  );
}
