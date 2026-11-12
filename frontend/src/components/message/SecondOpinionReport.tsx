import { useState } from "react";

import { costText, composerText, foldedLine, type SecondOpinion } from "./secondOpinionText";
import type { MessageActions } from "./MessageActions";
import "./secondOpinion.css";

function Details({ opinion, actions }: { opinion: SecondOpinion; actions: MessageActions }) {
  const reviewer = [opinion.reviewer_model, costText(opinion.cost)].filter(Boolean).join(" · ");
  const usable = opinion.status === "done";
  return (
    <div className="second-opinion-details">
      {opinion.summary && <p>{opinion.summary}</p>}
      {usable && opinion.agrees !== null && <p>{opinion.agrees ? "It agrees with the reply." : "It does not agree."}</p>}
      {opinion.findings.length > 0 && (
        <ul className="second-opinion-points">
          {opinion.findings.map((finding, index) => (
            <li key={index}>
              <span className={`second-opinion-severity second-opinion-${finding.severity}`}>{finding.severity}</span>{" "}
              {finding.path && (
                <code>
                  {finding.path}
                  {finding.line !== null ? `:${finding.line}` : ""}
                </code>
              )}{" "}
              {finding.explanation}
            </li>
          ))}
        </ul>
      )}
      {opinion.alternative && (
        <p>
          <strong>What it would do differently:</strong> {opinion.alternative}
        </p>
      )}
      {reviewer && <p className="faint small">Reviewer: {reviewer}</p>}
      <div className="second-opinion-buttons">
        {usable && actions.onUseInConversation && (
          <button
            type="button"
            className="button button-ghost"
            title="Put this in the message box, to send or edit"
            onClick={() => actions.onUseInConversation?.(composerText(opinion))}
          >
            Use in the conversation
          </button>
        )}
        {opinion.source === "reply" && actions.onOpenReview && (
          <button type="button" className="button button-ghost" onClick={() => actions.onOpenReview?.(opinion.id)}>
            Open in Reviews
          </button>
        )}
      </div>
    </div>
  );
}

/** A second opinion folded under the reply it is about; open it for the points and what the reviewer suggests. */
export function SecondOpinionReport({ opinion, actions }: { opinion: SecondOpinion; actions: MessageActions }) {
  const [open, setOpen] = useState(false);
  const hasDetails = opinion.status === "done";
  return (
    <div className={`second-opinion-report second-opinion-status-${opinion.status}`}>
      <button
        type="button"
        className="second-opinion-fold"
        aria-expanded={hasDetails ? open : undefined}
        disabled={!hasDetails}
        onClick={() => setOpen((value) => !value)}
      >
        {hasDetails && <span aria-hidden="true">{open ? "▾" : "▸"}</span>} {foldedLine(opinion)}
      </button>
      {hasDetails && open && <Details opinion={opinion} actions={actions} />}
    </div>
  );
}
