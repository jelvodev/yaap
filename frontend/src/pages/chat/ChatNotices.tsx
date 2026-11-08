import { Link } from "react-router-dom";

import type { Conversation, ModelInfo } from "../../api/client";

/** The model in use cannot be used in this environment yet, and why. */
export function ModelNotice({
  model,
  environmentName,
  hasConversation,
}: {
  model: ModelInfo;
  environmentName: string;
  hasConversation: boolean;
}) {
  return (
    <div className="notice">
      {model.display_name} cannot be used in the <strong>{environmentName}</strong> environment yet:{" "}
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
