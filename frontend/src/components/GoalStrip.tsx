import { useState } from "react";

import type { components } from "../api/schema";

export type Goal = components["schemas"]["GoalOut"];

const STATUS_LABELS: Record<Goal["status"], string> = {
  active: "Working on it",
  paused: "Paused",
  done: "Done",
  blocked: "Needs you",
  limit: "Limit reached",
  stopped: "Stopped",
};

/** The conversation's goal above the composer: progress, limits, pause, resume and stop; while it is
 * open, what earlier goals cost (estimate). */
export default function GoalStrip({
  goal,
  estimate = null,
  onChange,
  onDismiss,
}: {
  goal: Goal;
  estimate?: string | null;
  onChange: (body: components["schemas"]["GoalUpdate"]) => void;
  onDismiss: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [turns, setTurns] = useState(String(goal.max_turns));
  const [cost, setCost] = useState(String(goal.max_cost));
  const open = goal.status === "active" || goal.status === "paused";

  return (
    <div className={`goal-strip goal-${goal.status}`} aria-label="Goal">
      <div className="goal-head">
        <span className="goal-status">{STATUS_LABELS[goal.status]}</span>
        <span className="goal-objective" title={goal.objective}>
          {goal.objective}
        </span>
        <span className="goal-meter faint">
          turn {goal.turns} of {goal.max_turns}
          {goal.max_cost > 0 && ` · $${goal.cost.toFixed(2)} of $${goal.max_cost.toFixed(2)}`}
        </span>
        <span className="goal-actions">
          {open && (
            <button type="button" className="link-button" onClick={() => setEditing(!editing)}>
              Limits
            </button>
          )}
          {goal.status === "active" && (
            <button type="button" className="link-button" onClick={() => onChange({ status: "paused" })}>
              Pause
            </button>
          )}
          {goal.status === "paused" && (
            <button type="button" className="link-button" onClick={() => onChange({ status: "active" })}>
              Resume
            </button>
          )}
          {open ? (
            <button type="button" className="link-button danger-link" onClick={() => onChange({ status: "stopped" })}>
              Stop
            </button>
          ) : (
            <button type="button" className="link-button" onClick={onDismiss}>
              Close
            </button>
          )}
        </span>
      </div>
      {goal.summary && !open && <p className="goal-summary">{goal.summary}</p>}
      {goal.status === "paused" && <p className="goal-summary faint">YAAP sends no further turns until you resume.</p>}
      {open && estimate && <p className="goal-summary faint">{estimate}</p>}
      {editing && open && (
        <form
          className="goal-limits"
          onSubmit={(event) => {
            event.preventDefault();
            setEditing(false);
            onChange({ max_turns: Number(turns), max_cost: Number(cost) });
          }}
        >
          <label>
            Turns
            <input type="number" min={1} max={200} value={turns} onChange={(event) => setTurns(event.target.value)} />
          </label>
          <label>
            Cost ($, 0 = none)
            <input type="number" min={0} step={0.5} value={cost} onChange={(event) => setCost(event.target.value)} />
          </label>
          <button type="submit" className="button">
            Save
          </button>
        </form>
      )}
    </div>
  );
}
