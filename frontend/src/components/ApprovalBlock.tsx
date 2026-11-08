import { useState } from "react";

import { api, errorMessage } from "../api/client";
import type { ApprovalDecision } from "../api/message-stream";
import type { ReplyBlock } from "./message/replyBlock";

const DECISION_LABELS: Record<ApprovalDecision, string> = { approve: "Approved", deny: "Declined" };

/** An action the agent waits for: approve or decline, with an optional reason that the run's step keeps and that
 * learning proposals read. */
export default function ApprovalBlock({ block }: { block: Extract<ReplyBlock, { type: "approval" }> }) {
  const [sending, setSending] = useState<ApprovalDecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  async function decide(decision: ApprovalDecision) {
    setSending(decision);
    setError(null);
    const { error: requestError } = await api.POST("/api/v1/approvals/{approval_id}", {
      params: { path: { approval_id: block.id } },
      body: { decision, reason: reason.trim() || null },
    });
    if (requestError) {
      setError(errorMessage(requestError, "The decision could not be sent."));
      setSending(null);
    }
    // On success the stream reports the decision, which replaces the buttons.
  }

  return (
    <div className={`approval${block.decision ? ` approval-${block.decision}` : ""}`}>
      <div className="approval-head">
        <strong>{block.decision ? DECISION_LABELS[block.decision] : "Approval needed"}</strong>
        <span>{block.summary}</span>
      </div>
      {block.suspicion && (
        <p className="approval-suspicion" role="alert">
          Looks suspicious: {block.suspicion}
        </p>
      )}
      {block.reason && <p className="approval-reason">{block.reason}</p>}
      <pre className="approval-detail">{block.detail}</pre>
      {!block.decision && (
        <div className="approval-actions">
          <button type="button" className="button button-primary" disabled={sending !== null} onClick={() => void decide("approve")}>
            {sending === "approve" ? "Approving…" : "Approve"}
          </button>
          <button type="button" className="button" disabled={sending !== null} onClick={() => void decide("deny")}>
            {sending === "deny" ? "Declining…" : "Decline"}
          </button>
          <input
            className="approval-answer"
            value={reason}
            maxLength={500}
            disabled={sending !== null}
            aria-label="Reason (optional)"
            placeholder="Reason (optional), e.g. what to do instead"
            title="Kept with the decision; YAAP's learning proposals read it"
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
      )}
      {error && <div className="notice notice-error">{error}</div>}
    </div>
  );
}
