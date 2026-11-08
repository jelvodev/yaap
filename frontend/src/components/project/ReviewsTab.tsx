import ReviewReport from "./ReviewReport";
import { REVIEW_LABELS, type ReviewSummary, SOURCE_LABELS, reviewOutcome, useReviews } from "./useReviews";

function ReviewRow({ review, selected, onSelect }: { review: ReviewSummary; selected: boolean; onSelect: () => void }) {
  const when = new Date(review.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return (
    <div className="pp-task">
      <div className="pp-task-head">
        <button type="button" className="link-button" aria-expanded={selected} onClick={onSelect}>
          #{review.id} {SOURCE_LABELS[review.source]}
        </button>
        <span className={`pp-run-status pp-review-status-${review.status}`}>{REVIEW_LABELS[review.status]}</span>
        {review.status === "running" && <span className="running-dot" aria-label="Running" />}
        {review.status === "done" && <span className="faint small">{reviewOutcome(review)}</span>}
      </div>
      <span className="faint small">
        {when}
        {review.conversation_title && ` · ${review.conversation_title}`}
        {review.reviewer_model && ` · ${review.reviewer_model}`}
      </span>
    </div>
  );
}

/** The project's reviews by another vendor: each with its report beside the diff it reviewed. */
export default function ReviewsTab({ projectId, focus }: { projectId: number; focus: number | null }) {
  const reviews = useReviews(projectId, null, focus);
  const { detail } = reviews;

  if (reviews.reviews === null) return <p className="faint small pp-pad">Loading…</p>;
  return (
    <div className="pp-pad pp-tasks">
      <p className="faint small">
        A model of another vendor than the one that wrote the code reviews the changes, within the model policy. Goals and scheduled
        tasks that changed code are reviewed when they end; start one for the uncommitted changes under Changes, or for a
        conversation from its heading.
      </p>
      {reviews.reviews.length === 0 && <p className="faint small">No reviews yet.</p>}
      <ul className="pp-task-list">
        {reviews.reviews.map((review) => (
          <li key={review.id}>
            <ReviewRow
              review={review}
              selected={detail?.id === review.id}
              onSelect={() => reviews.select(detail?.id === review.id ? null : review.id)}
            />
            {detail?.id === review.id && <ReviewReport key={`${detail.id}-${detail.status}`} review={detail} />}
          </li>
        ))}
      </ul>
    </div>
  );
}
