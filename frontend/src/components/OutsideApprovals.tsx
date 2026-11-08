import { useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";

type Waiting = components["schemas"]["OutsideApprovalOut"];

const POLL_MS = 3000;

/**
 * Approvals an outside MCP client waits for in this conversation. They come from no reply stream,
 * so they are read every few seconds; after a decision the conversation reloads to show the logged call.
 */
export default function OutsideApprovals({ conversationId, onDecided }: { conversationId: number; onDecided: () => void }) {
  const [waiting, setWaiting] = useState<Waiting[]>([]);
  const [sending, setSending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data } = await api.GET("/api/v1/conversations/{conversation_id}/outside-approvals", {
      params: { path: { conversation_id: conversationId } },
    });
    setWaiting(data ?? []);
  }, [conversationId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  async function decide(approvalId: string, decision: "approve" | "deny") {
    setSending(approvalId);
    setError(null);
    const { error: requestError } = await api.POST("/api/v1/approvals/{approval_id}", {
      params: { path: { approval_id: approvalId } },
      body: { decision },
    });
    setSending(null);
    if (requestError) {
      setError(errorMessage(requestError, "The decision could not be sent."));
    }
    setWaiting((current) => current.filter((item) => item.approval_id !== approvalId));
    window.setTimeout(onDecided, 500);
  }

  if (waiting.length === 0 && !error) return null;
  return (
    <div className="outside-approvals">
      {waiting.map((item) => (
        <div key={item.approval_id} className="approval">
          <div className="approval-head">
            <strong>Approval needed for an outside MCP client</strong>
            <span>{item.summary}</span>
          </div>
          {item.suspicion && (
            <p className="approval-suspicion" role="alert">
              Looks suspicious: {item.suspicion}
            </p>
          )}
          {item.reason && <p className="approval-reason">{item.reason}</p>}
          <pre className="approval-detail">{item.detail}</pre>
          <div className="approval-actions">
            <button
              type="button"
              className="button button-primary"
              disabled={sending !== null}
              onClick={() => void decide(item.approval_id, "approve")}
            >
              Approve
            </button>
            <button
              type="button"
              className="button"
              disabled={sending !== null}
              onClick={() => void decide(item.approval_id, "deny")}
            >
              Decline
            </button>
          </div>
        </div>
      ))}
      {error && <div className="notice notice-error">{error}</div>}
    </div>
  );
}
