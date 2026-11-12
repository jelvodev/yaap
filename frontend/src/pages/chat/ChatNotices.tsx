import { Link } from "react-router-dom";

import type { Conversation, ConversationSummary, Environment, ModelInfo } from "../../api/client";
import { modelState } from "../../components/modelState";
import { returnNoticeText } from "./returnNoticeText";
import { type ReturnNoticeState, useReturnNotice } from "./useReturnNotice";

/** The model in use cannot be used here, and why: the model policy forbids it, or a key is missing. */
export function ModelNotice({
  model,
  environment,
  hasConversation,
}: {
  model: ModelInfo;
  environment: Environment;
  hasConversation: boolean;
}) {
  const state = modelState(model);
  if (state.kind === "not_allowed") {
    return (
      <div className="notice notice-warning">
        {model.display_name} is not allowed by the model policy in the <strong>{environment.name}</strong>{" "}
        environment. {state.reason} See{" "}
        <Link to={`/settings/models/policy?environment=${environment.id}`}>Settings › Models › Policy</Link>
        {hasConversation ? "." : ", or choose another model."}
      </div>
    );
  }
  return (
    <div className="notice">
      {model.display_name} cannot be used in the <strong>{environment.name}</strong> environment yet:{" "}
      {model.unavailable_reason} <Link to="/settings">Open Settings</Link>
      {hasConversation ? "." : ", or choose another model."}
    </div>
  );
}

/** A conversation that fills most of the model's context suggests starting a new one. */
export function ContextNotice({
  conversation,
  onNewConversation,
}: {
  conversation: Conversation;
  onNewConversation: () => void;
}) {
  const share =
    conversation.context_tokens && conversation.context_window
      ? Math.min(100, (conversation.context_tokens / conversation.context_window) * 100)
      : 0;
  if (share < 80) return null;
  return (
    <div className={`notice ${share >= 95 ? "notice-error" : "notice-warning"}`}>
      {share >= 95
        ? "This conversation has almost filled the model's context. "
        : `This conversation uses ${Math.round(share)}% of the model's context. `}
      For a new subject, a{" "}
      <button type="button" className="link-button" onClick={onNewConversation}>
        new conversation
      </button>{" "}
      starts fresh and costs less per message.
    </div>
  );
}

/** The cost of continuing after a pause, and what to do about it; sending as usual is the default. */
export function ReturnNotice({ state }: { state: ReturnNoticeState }) {
  const { notice, pending } = state;
  if (!notice) return null;
  return (
    <div className="notice notice-warning">
      {returnNoticeText(notice)}{" "}
      {pending ? (
        "Writing the summary…"
      ) : (
        <span className="return-notice-actions">
          {notice.can_summarise && (
            <button type="button" className="link-button" onClick={() => void state.summarise()}>
              Summarise first
            </button>
          )}
          <button type="button" className="link-button" onClick={() => void state.continueFromSummary()}>
            New conversation from a summary
          </button>
          <button type="button" className="link-button" onClick={state.dismiss}>
            Just send
          </button>
        </span>
      )}
      {state.error && <p className="form-error small">{state.error}</p>}
    </div>
  );
}

/** The notice for the open conversation; it asks again after each reply and stays away while one runs. */
export function ReturnNoticeFor({
  conversation,
  busy,
  onChanged,
  onContinued,
}: {
  conversation: Conversation;
  busy: boolean;
  onChanged: () => void;
  onContinued: (conversation: ConversationSummary) => void;
}) {
  const state = useReturnNotice(conversation.id, conversation.messages.length, busy, { onChanged, onContinued });
  return <ReturnNotice state={state} />;
}
