import { useState } from "react";
import { createPortal } from "react-dom";

import { FlaskIcon } from "../../components/icons";
import EvalCaseDialog from "./EvalCaseDialog";

/** Save as eval case, under the conversation's last reply: turns a conversation that went well into an eval case. */
export default function EvalCaseAction({
  conversationId,
  projectId,
  disabled,
}: {
  conversationId: number;
  projectId: number;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="message-action-labelled"
        disabled={disabled}
        title="Make an eval case of this conversation: its first message, the start before it and checks to edit"
        onClick={() => setOpen(true)}
      >
        <FlaskIcon size={13} />
        Save as eval case
      </button>
      {/* On the body, so the row it was opened from (shown on hover) cannot hide it. */}
      {open &&
        createPortal(
          <EvalCaseDialog conversationId={conversationId} projectId={projectId} onClose={() => setOpen(false)} />,
          document.body,
        )}
    </>
  );
}
