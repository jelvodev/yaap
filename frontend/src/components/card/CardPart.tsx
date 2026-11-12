import { Link, useNavigate } from "react-router-dom";

import PlanChecklist from "../plan/PlanChecklist";
import CardNextStep from "./CardNextStep";
import CardReviewPart from "./CardReviewPart";
import CardStageBar from "./CardStageBar";
import type { CardActionName } from "./cardLogic";
import { useCard } from "./useCard";
import "./card.css";

/** A card of the project board as the chat shows it: where it stands, its next step, and its plan or review. */
export default function CardPart({ projectId, cardId }: { projectId: number; cardId: number }) {
  const state = useCard(projectId, cardId);
  const navigate = useNavigate();
  const { card, plan, review, error } = state;

  async function act(action: CardActionName): Promise<void> {
    const result = await state.act(action);
    if (action === "plan" && result?.conversation_id) navigate(`/chat/${result.conversation_id}`);
  }

  if (!card) {
    return <div className="card-part card-part-loading">{error ?? "Loading the card…"}</div>;
  }
  return (
    <section className="card-part" aria-label={`Card #${card.id}`}>
      <header className="card-head">
        <span className="card-number">#{card.id}</span>
        <h3 className="card-title">{card.title}</h3>
        <span className="card-state" data-state={card.state}>
          {card.state}
        </span>
        <span className="card-cost">${card.cost_usd.toFixed(2)}</span>
      </header>
      <CardStageBar column={card.column} />
      <CardNextStep state={state} onAction={(action) => void act(action)} />
      {error && <p className="form-error small">{error}</p>}
      {card.error && <p className="form-error small">{card.error}</p>}
      <Link className="card-board-link" to={`/chat/projects/${projectId}/cards/${card.id}`}>
        Open on the board
      </Link>
      {(card.column === "plan" || card.column === "build") && plan && (
        <PlanChecklist plan={plan} busy={state.busy} onAnswer={state.answer} />
      )}
      {card.column === "review" && (
        <CardReviewPart
          card={card}
          review={review}
          plan={plan}
          busy={state.busy}
          onSendBack={(choice) => void state.sendBack(choice)}
        />
      )}
    </section>
  );
}
