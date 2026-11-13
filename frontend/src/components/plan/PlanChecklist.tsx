import { attemptsLabel, outsidePlanSentence, progressLine, shortCommit, uncommittedSentence } from "./planProgress";
import type { PlanOut } from "./usePlan";

type Step = PlanOut["steps"][number];
type Decision = "approve" | "deny";

const OUTCOME_LABELS = { passed: "passed", failed: "failed", not_run: "not run", stopped: "stopped" } as const;

function StepMark({ step }: { step: Step }) {
  if (step.status === "working") return <span className="plan-mark plan-spinner" role="img" aria-label="Working" />;
  const marks = { open: "○", done: "✓", failed: "✗", skipped: "–" } as const;
  const labels = { open: "Open", done: "Done", failed: "Failed", skipped: "Skipped" } as const;
  return (
    <span className="plan-mark" role="img" aria-label={labels[step.status]}>
      {marks[step.status]}
    </span>
  );
}

/**
 * The steps of a plan that is being built or was built: where each stands, how it went and
 * what the build waits for.
 */
export default function PlanChecklist({
  plan,
  busy,
  onAnswer,
}: {
  plan: PlanOut;
  busy: boolean;
  onAnswer: (approvalId: string, decision: Decision) => Promise<void>;
}) {
  const build = plan.build ?? null;
  const done = plan.steps.filter((step) => step.status === "done" || step.status === "skipped").length;
  const uncommitted = build ? uncommittedSentence(build.uncommitted) : "";
  return (
    <div className="plan-checklist">
      <p className="plan-progress-line">{progressLine(done, plan.steps.length, build?.cost ?? null)}</p>
      {plan.build_branch && plan.build_folder && (
        <p className="plan-build-info">
          Builds on <code>{plan.build_branch}</code> in <code>{plan.build_folder}</code>; your files and branch stay as
          they are. {uncommitted}
        </p>
      )}
      {build?.outside_plan.map((item) => (
        <p key={item.step} className="plan-build-info plan-outside" role="note">
          {outsidePlanSentence(item)}
        </p>
      ))}
      <ol className="plan-steps">
        {plan.steps.map((step) => {
          const attempts = attemptsLabel(build?.attempts[String(step.number)] ?? []);
          return (
            <li key={step.number} className="plan-step" data-status={step.status}>
              <div className="plan-step-head">
                <StepMark step={step} />
                <span className="plan-step-title">{step.title}</span>
                {step.commit && <code className="plan-commit">{shortCommit(step.commit)}</code>}
              </div>
              {attempts && <div className="plan-attempts">{attempts}</div>}
              {step.note && (
                <details className="plan-note">
                  <summary>Note</summary>
                  <p>{step.note}</p>
                </details>
              )}
            </li>
          );
        })}
      </ol>
      {build?.approvals.map((approval) => (
        <div key={approval.approval_id} className="plan-wait">
          <span>
            The build waits for approval: <strong>{approval.summary}</strong>
          </span>
          <div className="plan-actions">
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() => void onAnswer(approval.approval_id, "approve")}
            >
              Approve
            </button>
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => void onAnswer(approval.approval_id, "deny")}
            >
              Deny
            </button>
          </div>
        </div>
      ))}
      {build?.delivered && plan.status === "done" && plan.build_branch && (
        <p className="plan-build-info">
          The branch <code>{plan.build_branch}</code> is in the project.
        </p>
      )}
      {plan.status === "done" && build && build.final_checks.length > 0 && (
        <ul className="plan-final-checks" aria-label="Final checks">
          {build.final_checks.map((check, index) => (
            <li key={index} data-outcome={check.outcome} title={check.output || undefined}>
              <code className="plan-run">{check.command}</code>
              <span className="plan-outcome">{OUTCOME_LABELS[check.outcome]}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
