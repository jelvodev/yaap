import { useState } from "react";

import { api, errorMessage } from "../api/client";
import type { components } from "../api/schema";
import { type ContextUse, formatTokens } from "./composer/modelSetup";
import ContextPanel, { type StepLink } from "./context/ContextPanel";
import { type Breakdown, useContextBreakdown } from "./context/useContextBreakdown";
import TraceDialog from "./trace/TraceDialog";

export type ContextControl = components["schemas"]["ContextControlOut"];

const LIMIT_SOURCES: Record<ContextControl["source"], string> = {
  conversation: "chosen for this conversation",
  project: "from the project's settings",
  environment: "from the environment's settings",
  global: "from the global settings",
  default: "the model's window",
};

/** Where YAAP summarises by itself; not when it is off or the connection compacts itself. */
export function compactsAt(control: ContextControl | null | undefined): number | null {
  return control && control.auto_compact && !control.compacts_itself ? control.compact_at : null;
}

/**
 * Where the context goes and what the last reply cost, read while it is shown: the limit and its
 * source, how YAAP compacts, the parts, Summarise now, the project memory included, and the last reply.
 */
export default function ContextBreakdown({
  conversationId,
  use,
  control,
  version,
  onSummarised,
}: {
  conversationId: number;
  use: ContextUse;
  control: ContextControl | null;
  // Changes when a reply ends, so the breakdown refreshes.
  version: unknown;
  // After "Summarise now", so the chat shows the summary marker.
  onSummarised?: () => void;
}) {
  const { breakdown, error, reload } = useContextBreakdown(conversationId, version);
  const [traceAt, setTraceAt] = useState<StepLink | null>(null);
  const [summarising, setSummarising] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  async function summariseNow() {
    setSummarising(true);
    setSummaryError(null);
    const { error } = await api.POST("/api/v1/conversations/{conversation_id}/summary", {
      params: { path: { conversation_id: conversationId } },
    });
    setSummarising(false);
    if (error) {
      setSummaryError(errorMessage(error));
      return;
    }
    reload();
    onSummarised?.();
  }

  const { limit } = use;
  return (
    <>
      {control && limit && (
        <p className="faint small">
          Limit {formatTokens(limit)}, {LIMIT_SOURCES[control.source]}
          {use.lowerThanWindow ? ` · the model's window is ${formatTokens(use.window ?? 0)}` : ""}.
        </p>
      )}
      {control && limit && (
        <p className="faint small">
          {control.compacts_itself
            ? "This connection (Claude Code or Codex) compacts its own session; YAAP leaves it alone."
            : control.auto_compact
              ? `YAAP shortens old tool output at ${control.trim_at}% ` +
                `(${formatTokens((limit * control.trim_at) / 100)}) and summarises the earlier messages at ` +
                `${control.compact_at}% (${formatTokens((limit * control.compact_at) / 100)}), marked on the meter.`
              : "Automatic compaction is off: only /compact and Summarise now summarise."}
        </p>
      )}
      {error ? (
        <p className="form-error small">{error}</p>
      ) : !breakdown ? (
        <p className="muted small">Loading…</p>
      ) : (
        <BreakdownParts
          breakdown={breakdown}
          summarising={summarising}
          summaryError={summaryError}
          onSummarise={() => void summariseNow()}
          onOpenStep={setTraceAt}
        />
      )}
      {traceAt && (
        <TraceDialog
          target={{ conversationId, runId: traceAt.runId }}
          initialStepId={traceAt.stepId}
          onClose={() => setTraceAt(null)}
        />
      )}
    </>
  );
}

function BreakdownParts({
  breakdown,
  summarising,
  summaryError,
  onSummarise,
  onOpenStep,
}: {
  breakdown: Breakdown;
  summarising: boolean;
  summaryError: string | null;
  onSummarise: () => void;
  onOpenStep: (link: StepLink) => void;
}) {
  const last = breakdown.last_reply;
  return (
    <>
      <ContextPanel breakdown={breakdown} onOpenStep={onOpenStep} />
      <p className="faint small">
        The last reply used{" "}
        {breakdown.context_tokens !== null
          ? `${formatTokens(breakdown.context_tokens)} tokens of context`
          : "no context yet"}
        .
      </p>
      {breakdown.trimmed_tool_tokens > 0 && (
        <p className="faint small">
          Old tool output shortened · about {formatTokens(breakdown.trimmed_tool_tokens)} tokens saved; tool results
          count at their shortened size.
        </p>
      )}
      {(breakdown.can_summarise || breakdown.summarised_messages > 0) && (
        <div className="context-summary">
          <p className="faint small">
            {breakdown.summarised_messages > 0
              ? `The model gets a summary of the first ${breakdown.summarised_messages} messages ` +
                "instead of the messages themselves."
              : "Summarising keeps the last turns whole; /compact <focus> says what the summary should keep."}
          </p>
          {breakdown.can_summarise && (
            <button type="button" className="button" disabled={summarising} onClick={onSummarise}>
              {summarising ? "Summarising…" : "Summarise now"}
            </button>
          )}
          {summaryError && <p className="form-error small">{summaryError}</p>}
        </div>
      )}
      {breakdown.memory && !breakdown.memory.whole && (
        <details className="context-memory">
          <summary className="small">
            Project memory: {breakdown.memory.entries.length} of {breakdown.memory.total_entries} entries included
          </summary>
          <p className="faint small">
            The memory is too large to include whole. Included: the newest entries of each section and those closest
            to your last message; the agent searches the rest.
          </p>
          <ul>
            {breakdown.memory.entries.map((entry, index) => (
              <li key={index} className="small">
                <span className="faint">{entry.reason === "recent" ? "newest" : "related"} · </span>
                {entry.text}
              </li>
            ))}
          </ul>
        </details>
      )}
      {last && (
        <dl className="context-last">
          <dt>Last reply</dt>
          <dd className="mono">
            {formatTokens(last.input_tokens + last.cache_read_tokens + last.cache_write_tokens)} in ·{" "}
            {formatTokens(last.output_tokens)} out
          </dd>
          <dt>Read from cache</dt>
          <dd className="mono">{Math.round(last.cache_share * 100)}%</dd>
          <dt>Cost</dt>
          <dd className="mono">{last.cost === null ? "subscription" : `$${last.cost.toFixed(4)}`}</dd>
        </dl>
      )}
    </>
  );
}
