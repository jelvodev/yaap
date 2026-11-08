import { useMemo, useState } from "react";

import DiffView from "../DiffView";
import { diffPart, splitDiff } from "./diffParts";
import { money } from "./EvalRunDetail";
import { type Finding, type Review } from "./useReviews";

function FindingRow({ finding, selected, onSelect }: { finding: Finding; selected: boolean; onSelect: () => void }) {
  return (
    <li className="pp-review-finding">
      <div className="pp-task-head">
        <span className={`pp-review-severity pp-review-${finding.severity}`}>{finding.severity}</span>
        <button type="button" className="link-button mono small" aria-pressed={selected} onClick={onSelect} title="Show this file's diff">
          {finding.path}
          {finding.line !== null && `:${finding.line}`}
        </button>
      </div>
      <span className="small pp-review-explanation">{finding.explanation}</span>
    </li>
  );
}

/** A review's report beside the diff it reviewed: the findings, and the diff of the chosen file. */
export default function ReviewReport({ review }: { review: Review }) {
  const [file, setFile] = useState<string | null>(null);
  const parts = useMemo(() => splitDiff(review.diff), [review.diff]);
  const shownDiff = file ? diffPart(parts, file) || review.diff : review.diff;
  const reviewer = review.reviewer_model ? `${review.reviewer_model} (${review.reviewer_provider ?? "?"})` : null;
  const writers = review.writer_vendors.length ? review.writer_vendors.join(", ") : "nobody's model (no agent changes found)";

  return (
    <div className="pp-review">
      <div className="pp-review-report">
        <span className="faint small">
          Written by {writers}
          {reviewer && ` · reviewed by ${reviewer}`}
          {review.cost !== null && ` · ${money(review.cost)}`}
        </span>
        {review.objective && (
          <span className="small">
            <strong>Objective:</strong> {review.objective}
          </span>
        )}
        {review.reason && <p className={review.status === "failed" ? "form-error small" : "notice small"}>{review.reason}</p>}
        {review.summary && <p className="small">{review.summary}</p>}
        {review.notes.map((note) => (
          <span key={note} className="muted small">
            {note}
          </span>
        ))}
        {review.status === "done" && review.findings.length === 0 && <p className="muted small">The reviewer found nothing to report.</p>}
        {review.findings.length > 0 && (
          <ul className="pp-task-list">
            {review.findings.map((finding, index) => (
              <FindingRow
                key={`${finding.path}-${finding.line}-${index}`}
                finding={finding}
                selected={file === finding.path}
                onSelect={() => setFile(file === finding.path ? null : finding.path)}
              />
            ))}
          </ul>
        )}
        <p className="faint small">Nothing of a review is applied; change the code yourself or ask the agent.</p>
      </div>
      <div className="pp-diff">
        {file && (
          <button type="button" className="link-button small pp-review-all" onClick={() => setFile(null)}>
            Show the whole diff
          </button>
        )}
        <DiffView diff={shownDiff} />
      </div>
    </div>
  );
}
