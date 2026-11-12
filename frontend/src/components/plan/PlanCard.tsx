import { useState } from "react";

import { Markdown } from "../message/Markdown";
import PlanChecklist from "./PlanChecklist";
import PlanEditor from "./PlanEditor";
import { isFolded, planProgress, statusLabel } from "./planProgress";
import { type PlanEdit, type PlanOut, usePlan } from "./usePlan";
import "./plan.css";

function PlanSteps({ plan }: { plan: PlanOut }) {
  return (
    <ol className="plan-steps">
      {plan.steps.map((step) => (
        <li key={step.number} className="plan-step" data-status={step.status}>
          <div className="plan-step-head">
            <span className="plan-step-number">{step.number}</span>
            <span className="plan-step-title">{step.title}</span>
          </div>
          {step.details && (
            <div className="plan-step-details">
              <Markdown text={step.details} />
            </div>
          )}
          {step.files.length > 0 && (
            <div className="plan-files" aria-label="Files">
              {step.files.map((path) => (
                <code key={path} className="plan-file">
                  {path}
                </code>
              ))}
            </div>
          )}
          <ul className="plan-checks" aria-label="Checks">
            {step.checks.map((check, index) => (
              <li key={index}>
                {check.text}
                {check.run && <code className="plan-run">{check.run}</code>}
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}

/**
 * A plan the agent handed over with submit_plan, shown in the reply. A draft can be edited and discarded; approving
 * it builds it in a copy of the project. A discarded or replaced plan folds to one line.
 */
export default function PlanCard({ planId }: { planId: number }) {
  const { plan, error, invalid, busy, save, discard, approve, continueBuild, stop, answer } = usePlan(planId);
  const [editing, setEditing] = useState(false);
  const [unfolded, setUnfolded] = useState(false);

  if (!plan) {
    return <div className="plan-card plan-card-loading">{error ?? "Loading the plan…"}</div>;
  }
  const { done, total } = planProgress(plan.steps);
  const draft = plan.status === "draft";
  const tracked = ["building", "interrupted", "failed", "done"].includes(plan.status);
  const hasRunCommand = plan.steps.some((step) => step.checks.some((check) => check.run));

  if (isFolded(plan.status) && !unfolded) {
    return (
      <div className="plan-card plan-card-folded">
        <button type="button" className="link-button" onClick={() => setUnfolded(true)}>
          Plan {plan.id}: {plan.title}
        </button>
        <span className="plan-status" data-status={plan.status}>
          {statusLabel(plan.status)}
        </span>
      </div>
    );
  }

  async function saveEdit(edit: PlanEdit) {
    if (await save(edit)) setEditing(false);
  }

  return (
    <section className="plan-card" aria-label={`Plan ${plan.id}`}>
      <header className="plan-head">
        <span className="plan-kicker">Plan {plan.id}</span>
        <span className="plan-status" data-status={plan.status}>
          {statusLabel(plan.status)}
        </span>
        {plan.status !== "draft" && plan.status !== "cancelled" && (
          <span className="plan-progress">
            {done} of {total} steps
          </span>
        )}
      </header>
      {editing && draft ? (
        <PlanEditor plan={plan} busy={busy} invalid={invalid} onSave={saveEdit} onCancel={() => setEditing(false)} />
      ) : (
        <>
          <h3 className="plan-title">{plan.title}</h3>
          {plan.summary && <Markdown text={plan.summary} />}
          {tracked ? <PlanChecklist plan={plan} busy={busy} onAnswer={answer} /> : <PlanSteps plan={plan} />}
          {plan.risks.length > 0 && (
            <div className="plan-risks">
              <span className="plan-section-label">Risks</span>
              <ul>
                {plan.risks.map((risk) => (
                  <li key={risk}>{risk}</li>
                ))}
              </ul>
            </div>
          )}
          {plan.warnings.map((warning) => (
            <p key={warning} className="plan-warning">
              {warning}
            </p>
          ))}
          {plan.failure && <p className="plan-failure">{plan.failure}</p>}
          {draft && (
            <>
              <div className="plan-actions">
                <button type="button" className="button button-primary" disabled={busy} onClick={() => void approve()}>
                  Approve and build
                </button>
                <button type="button" className="button" disabled={busy} onClick={() => setEditing(true)}>
                  Edit
                </button>
                <button type="button" className="button button-danger" disabled={busy} onClick={() => void discard()}>
                  Discard
                </button>
              </div>
              {hasRunCommand && (
                <p className="plan-hint">
                  Approving runs these check commands in a copy of the project without asking.
                </p>
              )}
            </>
          )}
          {(plan.status === "building" || plan.status === "interrupted" || plan.status === "failed") && (
            <div className="plan-actions">
              {plan.status === "building" ? (
                <button type="button" className="button button-danger" disabled={busy} onClick={() => void stop()}>
                  Stop
                </button>
              ) : (
                <button
                  type="button"
                  className="button button-primary"
                  disabled={busy}
                  onClick={() => void continueBuild()}
                >
                  Continue
                </button>
              )}
            </div>
          )}
        </>
      )}
      {error && (
        <p className="plan-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
