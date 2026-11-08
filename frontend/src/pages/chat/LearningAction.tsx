import { BulbIcon } from "../../components/icons";
import { useLearningRequest } from "./useLearningRequest";

/** Learn from this, under the conversation's last reply: learning proposals from the whole conversation. */
export default function LearningAction({ conversationId, disabled }: { conversationId: number; disabled: boolean }) {
  const learning = useLearningRequest(conversationId);
  return (
    <>
      <button
        type="button"
        className="message-action-labelled"
        disabled={disabled || learning.busy}
        title="Propose rules, memory lines or skill steps from what went wrong in this conversation"
        onClick={() => void learning.request()}
      >
        <BulbIcon size={13} />
        {learning.busy ? "Learning…" : "Learn from this"}
      </button>
      {learning.result && (
        <span className="message-action-note" role="status">
          {learning.result}
        </span>
      )}
    </>
  );
}
