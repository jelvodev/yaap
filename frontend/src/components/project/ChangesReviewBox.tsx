import { CloseIcon } from "../icons";
import type { ChangesReview } from "./useChangesReview";
import { REVIEW_LABELS, reviewOutcome } from "./useReviews";

/** The review of these changes at the top of the list: its state, its findings, and a link to the full report. */
export default function ChangesReviewBox({
  review: { review, error, dismiss },
  onShowFile,
  onOpenReport,
}: {
  review: ChangesReview;
  /** Open a file's diff in the list, from a finding. */
  onShowFile: (path: string) => void;
  onOpenReport: (reviewId: number) => void;
}) {
  if (!review && !error) return null;
  return (
    <section className="cp-review" aria-label="Review of these changes">
      <div className="cp-review-head">
        <strong className="small">Review</strong>
        {review && (
          <>
            {review.status === "running" && <span className="running-dot" aria-label="Running" />}
            <span className="faint small">
              {review.status === "done" ? reviewOutcome(review) : REVIEW_LABELS[review.status]}
            </span>
            <button
              type="button"
              className="link-button small cp-review-report"
              onClick={() => onOpenReport(review.id)}
            >
              Full report
            </button>
          </>
        )}
        <button type="button" className="icon-button cp-review-close" aria-label="Hide the review" onClick={dismiss}>
          <CloseIcon size={13} />
        </button>
      </div>
      {error && <p className="form-error small">{error}</p>}
      {review?.reason && (
        <p className={review.status === "failed" ? "form-error small" : "notice small"}>{review.reason}</p>
      )}
      {review?.summary && <p className="small">{review.summary}</p>}
      {review && review.findings.length > 0 && (
        <ul className="cp-findings">
          {review.findings.map((finding, index) => (
            <li key={`${finding.path}-${finding.line}-${index}`}>
              <span className={`pp-review-severity pp-review-${finding.severity}`}>{finding.severity}</span>
              <button
                type="button"
                className="link-button mono small"
                title="Show this file's diff"
                onClick={() => onShowFile(finding.path)}
              >
                {finding.path}
                {finding.line !== null && `:${finding.line}`}
              </button>
              <span className="small pp-review-explanation">{finding.explanation}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
