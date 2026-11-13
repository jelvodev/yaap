import { useMakeCard } from "../card/useMakeCard";
import type { RuleTarget } from "../RulePanel";

/** Make a card on the project board from this message; shows nothing outside a project conversation. */
export default function MakeCardAction({ messageId, target }: { messageId: number; target?: RuleTarget }) {
  if (!target?.projectId || messageId <= 0) return null;
  return <MakeCardButton conversationId={target.conversationId} messageId={messageId} />;
}

function MakeCardButton({ conversationId, messageId }: { conversationId: number; messageId: number }) {
  const { made, error, busy, make } = useMakeCard(conversationId, messageId);
  if (made !== null) return <span className="card-chip-made">Card #{made} made</span>;
  return (
    <>
      <button
        type="button"
        disabled={busy}
        title="Make a card on the project board from this message"
        onClick={() => void make()}
      >
        Make a card
      </button>
      {error && <span className="form-error small">{error}</span>}
    </>
  );
}
