import type { CardActionName } from "./cardLogic";
import { nextButtons, nextLabel } from "./cardLogic";
import type { CardState } from "./useCard";

/** What the card waits for, who has to act, and the one button that takes the step. */
export default function CardNextStep({
  state,
  onAction,
}: {
  state: CardState;
  onAction: (action: CardActionName) => void;
}) {
  const { card, plan, planMissing, review, busy } = state;
  if (!card) return null;
  const nextAction = card.next.action;
  const label = nextLabel(card.next.label, nextAction, plan?.steps ?? null, planMissing);
  const buttons = nextButtons({
    nextAction,
    planStatus: plan?.status ?? card.plan?.status ?? null,
    reviewId: review?.status === "failed" ? card.review_id : null,
    planMissing,
  });
  return (
    <div className="card-next">
      <span className="card-next-label" data-who={card.next.who}>
        {label}
      </span>
      {buttons.map((button) => (
        <button
          key={button.action}
          type="button"
          className={button.action === "stop" ? "button" : "button button-primary"}
          disabled={busy}
          onClick={() => onAction(button.action)}
        >
          {button.label}
        </button>
      ))}
    </div>
  );
}
