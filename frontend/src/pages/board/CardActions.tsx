import "../../components/card/card.css";
import "../../components/plan/plan.css";

import { useNavigate } from "react-router-dom";

import CardReviewPart from "../../components/card/CardReviewPart";
import type { CardActionName } from "../../components/card/cardLogic";
import { type CardState, useCard } from "../../components/card/useCard";
import BuildNotice, { showsChecklist } from "../../components/card/BuildNotice";
import MergePart from "../../components/card/MergePart";
import PlanChecklist from "../../components/plan/PlanChecklist";
import type { PlanOut } from "../../components/plan/usePlan";
import type { CardDetail, Column } from "./boardApi";
import { type ColumnButton, columnButtons } from "./boardLogic";

interface Props {
  projectId: number;
  card: CardDetail;
  /** A hand move of the card (Move back to Idea). */
  onMove: (column: Column) => Promise<boolean>;
  /** The card changed: the board and the card detail read it again. */
  onChanged: () => Promise<void>;
}

const LABELS: Record<ColumnButton, string> = {
  specify: "Specify",
  plan: "Plan",
  move_to_idea: "Move back to Idea",
  build: "Approve and build",
  edit_plan: "Edit plan",
  ask_change: "Ask a change",
  stop: "Stop",
  continue: "Continue",
  send_back: "Send back",
  review: "Review",
  mark_done: "Mark done",
};

const STAGE_ACTIONS: Partial<Record<ColumnButton, CardActionName>> = {
  specify: "specify",
  plan: "plan",
  build: "build",
  stop: "stop",
  continue: "continue",
  review: "review",
  mark_done: "mark_done",
};

/** The commands approving the plan runs without asking: its steps' checks and the card's criteria commands. */
function PlanCommands({ plan }: { plan: PlanOut }) {
  const commands = [
    ...plan.steps.flatMap((step) => step.checks.flatMap((check) => (check.run ? [check.run] : []))),
    ...plan.criteria_commands,
  ];
  const unique = [...new Set(commands)];
  if (unique.length === 0) return null;
  return (
    <div className="plan-criteria-commands">
      <span className="plan-section-label">Approving runs these commands in a copy of the project without asking:</span>
      <ul>
        {unique.map((command) => (
          <li key={command}>
            <code className="plan-run">{command}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** What the card's column shows above its buttons: the plan's commands, the build, the review or the result. */
function ColumnView({
  projectId,
  card,
  state,
  onChanged,
}: {
  projectId: number;
  card: CardDetail;
  state: CardState;
  onChanged: () => Promise<void>;
}) {
  const { plan, review, busy } = state;
  const status = plan?.status ?? card.plan?.status ?? null;
  if (card.column === "plan" && plan && status === "draft") return <PlanCommands plan={plan} />;
  if (card.column === "build" && plan) {
    const reason =
      status === "interrupted" ? "YAAP restarted or the build was stopped." : status === "failed" ? plan.failure : null;
    return (
      <>
        {reason && <p className="plan-failure">{reason}</p>}
        <BuildNotice plan={plan} />
        {showsChecklist(plan) && <PlanChecklist plan={plan} busy={busy} onAnswer={state.answer} />}
      </>
    );
  }
  if (card.column === "review") {
    return (
      <>
        <CardReviewPart
          projectId={projectId}
          card={card}
          review={review}
          plan={plan}
          busy={busy}
          onSendBack={(choice) => void state.sendBack(choice)}
        />
        {card.version && <MergePart projectId={projectId} cardId={card.id} onChanged={onChanged} />}
      </>
    );
  }
  if (card.column === "done") {
    return (
      <>
        <div className="card-detail-box">
          {review?.summary && <p>{review.summary}</p>}
          {card.links.branch ? (
            <div>
              Built on the branch <code>{card.links.branch}</code>.
            </div>
          ) : (
            <div className="faint">No build branch.</div>
          )}
        </div>
        {card.version && <MergePart projectId={projectId} cardId={card.id} onChanged={onChanged} />}
      </>
    );
  }
  return null;
}

/** A card's stage buttons on the board, per column, with the part of the card that stage works on. */
export default function CardActions({ projectId, card, onMove, onChanged }: Props) {
  const state = useCard(projectId, card.id);
  const navigate = useNavigate();
  const { plan, planMissing, busy, error } = state;
  const buttons = columnButtons({
    column: card.column,
    broken: card.broken,
    planStatus: plan?.status ?? card.plan?.status ?? null,
    planMissing,
    review: card.review,
    blockedBy: card.blocked_by,
  });

  async function press(button: ColumnButton): Promise<void> {
    if (button === "move_to_idea") {
      await onMove("idea");
      return;
    }
    if (button === "edit_plan" || button === "ask_change") {
      // The plan is edited, or a change asked, in the conversation it was made in.
      if (plan) navigate(`/chat/${plan.conversation_id}`);
      return;
    }
    if (button === "send_back") {
      await state.sendBack({});
      await onChanged();
      return;
    }
    const action = STAGE_ACTIONS[button];
    if (!action) return;
    const result = await state.act(action);
    if (!result) return;
    if ((action === "specify" || action === "plan") && result.conversation_id) {
      navigate(`/chat/${result.conversation_id}`);
      return;
    }
    await onChanged();
  }

  const label = (button: ColumnButton): string =>
    button === "review" && card.review_id !== null ? "Review again" : LABELS[button];

  return (
    <section className="card-detail-section">
      <ColumnView projectId={projectId} card={card} state={state} onChanged={onChanged} />
      {error && <div className="form-error">{error}</div>}
      {buttons.length > 0 && (
        <div className="card-detail-actions">
          {buttons.map((button, index) => (
            <button
              key={button}
              type="button"
              className={
                button === "stop" ? "button button-danger" : index === 0 ? "button button-primary" : "button"
              }
              disabled={busy || ((button === "edit_plan" || button === "ask_change") && !plan)}
              onClick={() => void press(button)}
            >
              {label(button)}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
