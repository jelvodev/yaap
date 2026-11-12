import { Link, useLocation } from "react-router-dom";

import type { PlanOut } from "../plan/usePlan";
import EvidencePart from "./EvidencePart";
import type { CardDetail, ReviewDetail, SendBackChoice } from "./useCard";

const REVIEW_STATUS = {
  running: "Review running",
  done: "Review done",
  skipped: "Review skipped",
  failed: "Review failed",
} as const;

const OUTCOME_LABELS = { passed: "passed", failed: "failed", not_run: "not run", stopped: "stopped" } as const;

function Checks({ plan }: { plan: PlanOut }) {
  const stepChecks = plan.steps.flatMap((step) =>
    step.checks.flatMap((check) => (check.run ? [{ step, command: check.run }] : [])),
  );
  const finalChecks = plan.build?.final_checks ?? [];
  if (stepChecks.length === 0 && finalChecks.length === 0) return null;
  return (
    <div className="card-checks">
      <h4>Checks that ran</h4>
      <ul>
        {stepChecks.map(({ step, command }) => (
          <li key={`${step.number}-${command}`} data-outcome={step.status}>
            <code>{command}</code>
            <span>
              step {step.number}, {step.status}
            </span>
          </li>
        ))}
        {finalChecks.map((check, index) => (
          <li key={`final-${index}`} data-outcome={check.outcome} title={check.output || undefined}>
            <code>{check.command}</code>
            <span>final, {OUTCOME_LABELS[check.outcome]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The review of a card: the reviewer's summary and findings as plain text, the evidence behind it, and the branch. */
export default function CardReviewPart({
  card,
  review,
  plan,
  busy,
  onSendBack,
}: {
  card: CardDetail;
  review: ReviewDetail | null;
  plan: PlanOut | null;
  busy: boolean;
  onSendBack: (choice: SendBackChoice) => void;
}) {
  // The Branches panel opens through ?panel= on the conversation the card is shown in; it has no branch preselection.
  const { pathname } = useLocation();
  const branch = card.links.branch;
  const evidence = review?.evidence ?? null;
  const branchLink = branch && (
    <>
      Branch <code>{branch}</code>. <Link to={`${pathname}?panel=branches`}>Open the Branches panel</Link> to see the
      diff.
    </>
  );
  return (
    <div className="card-review">
      <p className="card-review-status" data-status={review?.status ?? card.review}>
        {review ? REVIEW_STATUS[review.status] : card.review === "none" ? "Not reviewed yet" : "Review"}
      </p>
      {review?.reason && <p className="card-muted">{review.reason}</p>}
      {review?.summary && <p className="card-review-summary">{review.summary}</p>}
      {review && review.findings.length > 0 && (
        <ul className="card-findings" aria-label="Findings">
          {review.findings.map((finding, index) => (
            <li key={index} data-severity={finding.severity}>
              <code>{finding.line === null ? finding.path : `${finding.path}:${finding.line}`}</code>
              <span className="card-finding-severity">{finding.severity}</span>
              <p>{finding.explanation}</p>
              <button
                type="button"
                className="link-button"
                disabled={busy}
                onClick={() => onSendBack({ finding: index })}
              >
                Send back with the finding
              </button>
            </li>
          ))}
        </ul>
      )}
      {evidence ? (
        <EvidencePart
          evidence={evidence}
          branch={branchLink}
          busy={busy}
          onSendBack={(criterion) => onSendBack({ criterion })}
        />
      ) : (
        <>
          {plan && <Checks plan={plan} />}
          {branchLink && <p className="card-muted">{branchLink}</p>}
        </>
      )}
    </div>
  );
}
