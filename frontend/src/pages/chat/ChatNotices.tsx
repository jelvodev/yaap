import { Link } from "react-router-dom";

import type { Conversation, Environment, ModelInfo } from "../../api/client";
import { modelState } from "../../components/modelState";

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
